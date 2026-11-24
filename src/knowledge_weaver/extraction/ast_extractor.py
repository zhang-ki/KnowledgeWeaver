from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from knowledge_weaver.core.ids import canonical_json, entity_id
from knowledge_weaver.models import (
    Document,
    Entity,
    EntityKind,
    ExtractionRequest,
    ExtractionResult,
    Polarity,
    ProvenanceMethod,
    RelationPredicate,
)

from ._common import ExtractionBuilder


@dataclass(frozen=True)
class _Frame:
    qualname: str
    entity: Entity
    class_entity: Entity | None


@dataclass(frozen=True)
class _ImportBinding:
    entity: Entity
    qualified_prefix: str
    is_module: bool
    resolvable: bool = True


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    offsets.extend(index + 1 for index, character in enumerate(text) if character == "\n")
    return offsets


def _absolute_offset(
    text: str, line_offsets: list[int], line: int | None, byte_column: int | None
) -> int | None:
    if line is None or byte_column is None or line < 1 or line > len(line_offsets):
        return None
    line_start = line_offsets[line - 1]
    line_end = line_offsets[line] if line < len(line_offsets) else len(text)
    line_text = text[line_start:line_end]
    try:
        character_column = len(line_text.encode("utf-8")[:byte_column].decode("utf-8"))
    except UnicodeDecodeError:
        return None
    if character_column > len(line_text):
        return None
    return line_start + character_column


def _node_range(node: ast.AST, text: str, line_offsets: list[int]) -> tuple[int, int] | None:
    start = _absolute_offset(
        text,
        line_offsets,
        getattr(node, "lineno", None),
        getattr(node, "col_offset", None),
    )
    end = _absolute_offset(
        text,
        line_offsets,
        getattr(node, "end_lineno", None),
        getattr(node, "end_col_offset", None),
    )
    if start is None or end is None or end <= start:
        return None
    return start, end


def _symbol_name_range(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    text: str,
    line_offsets: list[int],
) -> tuple[int, int] | None:
    start = _absolute_offset(text, line_offsets, node.lineno, node.col_offset)
    if start is None:
        return None
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    line = text[start:line_end]
    prefix = re.match(r"(?:(?:async)[ \t]+)?(?:def|class)[ \t]+", line)
    if prefix is None:
        return None
    name_start = start + prefix.end()
    name_end = name_start + len(node.name)
    if text[name_start:name_end] != node.name:
        return None
    return start, name_end


def _module_name(relative_path: str) -> str:
    path = PurePosixPath(relative_path.replace("\\", "/"))
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    safe_parts = [part for part in parts if part and part != "."]
    return ".".join(safe_parts) or path.stem or "<module>"


def _namespace(source_id: str) -> str:
    return f"python:{source_id}"


def _identity_key(source_id: str, module_path: str, qualname: str, kind: EntityKind) -> str:
    return canonical_json(
        {
            "source_id": source_id,
            "module_path": module_path,
            "qualname": qualname,
            "kind": kind.value,
        }
    )


@dataclass(frozen=True)
class _IndexedDefinition:
    entity: Entity
    module_name: str


@dataclass(frozen=True)
class _PythonSourceIndex:
    source_id: str
    source_version_id: str
    corpus_id: str
    definitions: Mapping[str, tuple[_IndexedDefinition, ...]]
    module_counts: Mapping[str, int]
    opaque_exports: frozenset[str]
    opaque_modules: frozenset[str]

    def matches(self, *, source_id: str, source_version_id: str | None, corpus_id: str) -> bool:
        return (
            source_id == self.source_id
            and source_version_id == self.source_version_id
            and corpus_id == self.corpus_id
        )

    def resolve(
        self,
        qualified_name: str,
        *,
        expected: EntityKind,
        source_id: str,
        source_version_id: str | None,
        corpus_id: str,
    ) -> Entity | None:
        if not self.matches(
            source_id=source_id,
            source_version_id=source_version_id,
            corpus_id=corpus_id,
        ):
            return None
        parts = qualified_name.split(".")
        if not qualified_name or any(not part for part in parts):
            return None
        prefixes = {".".join(parts[:index]) for index in range(1, len(parts) + 1)}
        if prefixes.intersection(self.opaque_modules | self.opaque_exports):
            return None
        candidates = self.definitions.get(qualified_name, ())
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        module_parts = candidate.module_name.split(".")
        if any(
            self.module_counts.get(".".join(module_parts[:index]), 0) > 1
            for index in range(1, len(module_parts) + 1)
        ):
            return None
        if self.module_counts.get(candidate.module_name) != 1:
            return None
        if expected is EntityKind.function:
            if candidate.entity.kind not in {EntityKind.function, EntityKind.class_}:
                return None
        elif candidate.entity.kind is not expected:
            return None
        return candidate.entity


class _DefinitionBindingCollector(ast.NodeVisitor):
    """Collect names bound in one module or class execution scope."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    @classmethod
    def _target_names(cls, target: ast.AST) -> set[str]:
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            return set().union(*(cls._target_names(item) for item in target.elts))
        return set()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.names.update(self._target_names(target))
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.names.update(self._target_names(node.target))
        if node.value is not None:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.names.update(self._target_names(node.target))
        self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.names.update(self._target_names(node.target))
        self.visit(node.value)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.names.update(self._target_names(target))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            return
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.bases, *(item.value for item in node.keywords)):
            self.visit(expression)

    def visit_For(self, node: ast.For) -> None:
        self.names.update(self._target_names(node.target))
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.names.update(self._target_names(node.target))
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self.names.update(self._target_names(item.optional_vars))
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self.names.update(self._target_names(item.optional_vars))
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.names.add(node.name)
        if node.pattern is not None:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda body has its own scope; its local bindings cannot rebind this scope.
        for expression in (*node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)


class _DefinitionIndexVisitor(ast.NodeVisitor):
    def __init__(
        self,
        document: Document,
        *,
        corpus_id: str,
        module_name: str,
        definitions: dict[str, list[_IndexedDefinition]],
        opaque_exports: set[str],
    ) -> None:
        self.document = document
        self.corpus_id = corpus_id
        self.module_name = module_name
        self.definitions = definitions
        self.opaque_exports = opaque_exports

    def _index_definition(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: EntityKind,
        qualname: str,
    ) -> None:
        identity = _identity_key(
            self.document.source_id,
            self.document.relative_path,
            qualname,
            kind,
        )
        entity = Entity(
            id=entity_id(self.corpus_id, kind.value, identity),
            corpus_id=self.corpus_id,
            kind=kind,
            canonical_name=qualname,
            namespace=_namespace(self.document.source_id),
            identity_key=identity,
            aliases=[node.name],
        )
        self.definitions.setdefault(qualname, []).append(
            _IndexedDefinition(entity=entity, module_name=self.module_name)
        )

    def _mark_scope_writes_opaque(self, statement: ast.stmt, *, prefix: str) -> set[str]:
        collector = _DefinitionBindingCollector()
        collector.visit(statement)
        for name in collector.names:
            self.opaque_exports.add(f"{prefix}.{name}")
        return collector.names

    def _index_class_members(self, node: ast.ClassDef, class_qualname: str) -> None:
        binding_counts: dict[str, int] = defaultdict(int)
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                binding_counts[statement.name] += 1
                self._index_definition(
                    statement,
                    EntityKind.function,
                    f"{class_qualname}.{statement.name}",
                )
            else:
                for name in self._mark_scope_writes_opaque(statement, prefix=class_qualname):
                    binding_counts[name] += 1
        for name, count in binding_counts.items():
            if count != 1:
                self.opaque_exports.add(f"{class_qualname}.{name}")

    def visit_Module(self, node: ast.Module) -> None:
        binding_counts: dict[str, int] = defaultdict(int)
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                binding_counts[statement.name] += 1
                self._index_definition(
                    statement,
                    EntityKind.function,
                    f"{self.module_name}.{statement.name}",
                )
            elif isinstance(statement, ast.ClassDef):
                binding_counts[statement.name] += 1
                class_qualname = f"{self.module_name}.{statement.name}"
                self._index_definition(statement, EntityKind.class_, class_qualname)
                self._index_class_members(statement, class_qualname)
            else:
                for name in self._mark_scope_writes_opaque(statement, prefix=self.module_name):
                    binding_counts[name] += 1
        for name, count in binding_counts.items():
            if count != 1:
                self.opaque_exports.add(f"{self.module_name}.{name}")


def _package_name(relative_path: str) -> str:
    path = PurePosixPath(relative_path.replace("\\", "/"))
    module_name = _module_name(relative_path)
    if path.stem == "__init__":
        return module_name
    return module_name.rpartition(".")[0]


def _resolve_import_module(
    *, relative_path: str, level: int, imported_module: str | None
) -> str | None:
    if level == 0:
        return imported_module or ""
    package_parts = [part for part in _package_name(relative_path).split(".") if part]
    keep = len(package_parts) - level + 1
    if not package_parts or keep < 1:
        return None
    return ".".join([*package_parts[:keep], *(imported_module or "").split(".")]).strip(".")


class _ReExportVisitor(ast.NodeVisitor):
    """Record module-level import aliases that may be re-exported to callers."""

    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.opaque_exports: set[str] = set()
        self.opaque_modules: set[str] = set()
        self.scope_depth = 0

    def visit_Import(self, node: ast.Import) -> None:
        if self.scope_depth == 0:
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                self.opaque_exports.add(f"{self.module_name}.{local_name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.scope_depth != 0:
            return
        if any(alias.name == "*" for alias in node.names):
            self.opaque_modules.add(self.module_name)
            return
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.opaque_exports.add(f"{self.module_name}.{local_name}")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.scope_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.scope_depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.scope_depth -= 1


def _build_python_source_index(
    *,
    source_id: str,
    source_version_id: str,
    corpus_id: str,
    documents: Iterable[Document],
) -> _PythonSourceIndex:
    definitions: dict[str, list[_IndexedDefinition]] = defaultdict(list)
    module_counts: dict[str, int] = defaultdict(int)
    opaque_exports: set[str] = set()
    opaque_modules: set[str] = set()
    for document in documents:
        if (
            document.source_id != source_id
            or not document.relative_path.casefold().endswith((".py", ".pyi"))
            or not document.normalized_text.strip()
        ):
            continue
        try:
            tree = ast.parse(document.normalized_text, filename=document.relative_path)
        except (SyntaxError, ValueError, TypeError):
            continue
        module_name = _module_name(document.relative_path)
        module_counts[module_name] += 1
        module_identity = _identity_key(
            source_id, document.relative_path, module_name, EntityKind.module
        )
        module_entity = Entity(
            id=entity_id(corpus_id, EntityKind.module.value, module_identity),
            corpus_id=corpus_id,
            kind=EntityKind.module,
            canonical_name=module_name,
            namespace=_namespace(source_id),
            identity_key=module_identity,
        )
        definitions[module_name].append(
            _IndexedDefinition(entity=module_entity, module_name=module_name)
        )
        _DefinitionIndexVisitor(
            document,
            corpus_id=corpus_id,
            module_name=module_name,
            definitions=definitions,
            opaque_exports=opaque_exports,
        ).visit(tree)
        reexports = _ReExportVisitor(module_name)
        reexports.visit(tree)
        opaque_exports.update(reexports.opaque_exports)
        opaque_modules.update(reexports.opaque_modules)
    return _PythonSourceIndex(
        source_id=source_id,
        source_version_id=source_version_id,
        corpus_id=corpus_id,
        definitions={name: tuple(items) for name, items in definitions.items()},
        module_counts=dict(module_counts),
        opaque_exports=frozenset(opaque_exports),
        opaque_modules=frozenset(opaque_modules),
    )


class _StaticExtractor(ast.NodeVisitor):
    def __init__(
        self,
        request: ExtractionRequest,
        builder: ExtractionBuilder,
        module_entity: Entity,
        provenance: Any,
        source_index: _PythonSourceIndex | None,
        source_version_id: str | None,
    ) -> None:
        self.request = request
        self.builder = builder
        self.document = request.document
        self.text = request.document.normalized_text
        self.line_offsets = _line_offsets(self.text)
        self.module_name = _module_name(self.document.relative_path)
        self.module_entity = module_entity
        self.provenance = provenance
        self.source_index = source_index
        self.source_version_id = source_version_id
        self.frames = [_Frame(self.module_name, module_entity, None)]
        self.symbols_by_scope: dict[str, dict[str, Entity]] = {self.module_name: {}}
        self.class_members: dict[str, dict[str, Entity]] = {}
        self.imports_by_scope: dict[str, dict[str, _ImportBinding]] = {}
        self.shadowed_by_scope: dict[str, set[str]] = {}
        self.star_import_scopes: set[str] = set()

    @property
    def frame(self) -> _Frame:
        return self.frames[-1]

    def _push(self, entity: Entity, qualname: str, class_entity: Entity | None) -> None:
        self.frames.append(_Frame(qualname, entity, class_entity))
        self.symbols_by_scope.setdefault(qualname, {})
        if class_entity is not None:
            self.class_members.setdefault(class_entity.id, {})

    def _pop(self) -> None:
        self.frames.pop()

    def _visit_outer_expressions(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for item in node.decorator_list:
            self.visit(item)
        args = node.args
        for default in args.defaults:
            self.visit(default)
        for kw_default in args.kw_defaults:
            if kw_default is not None:
                self.visit(kw_default)
        for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if args.vararg is not None and args.vararg.annotation is not None:
            self.visit(args.vararg.annotation)
        if args.kwarg is not None and args.kwarg.annotation is not None:
            self.visit(args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    def _visit_outer_class_expressions(self, node: ast.ClassDef) -> None:
        for item in node.decorator_list:
            self.visit(item)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        entity = self._register_definition(node, EntityKind.class_)
        self._visit_outer_class_expressions(node)
        if entity is None:
            return
        self._push(entity, entity.canonical_name, entity)
        for statement in node.body:
            self.visit(statement)
        self._pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        entity = self._register_definition(node, EntityKind.function)
        self._visit_outer_expressions(node)
        if entity is None:
            return
        class_entity = self.frame.class_entity
        self._push(entity, entity.canonical_name, class_entity)
        for statement in node.body:
            self.visit(statement)
        self._pop()

    def _register_definition(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: EntityKind,
    ) -> Entity | None:
        name_range = _symbol_name_range(node, self.text, self.line_offsets)
        qualname = f"{self.frame.qualname}.{node.name}"
        if name_range is None:
            self.builder.reject(
                "AST_SYMBOL_UNLOCATED",
                f"Static analyzer could not locate a {kind.value} name at line {node.lineno}.",
            )
            return None
        evidence = self.builder.evidence_at_doc_range(
            name_range[0], name_range[1], self.provenance
        )
        if evidence is None:
            self.builder.reject(
                "SOURCE_SPAN_UNAVAILABLE",
                f"No input chunk span covers a {kind.value} name at line {node.lineno}.",
            )
            return None
        entity = self.builder.add_entity(
            kind,
            qualname,
            _namespace(self.document.source_id),
            _identity_key(
                self.document.source_id,
                self.document.relative_path,
                qualname,
                kind,
            ),
            aliases=[node.name],
        )
        self.builder.add_mention(entity, evidence, node.name)
        parent = self.frame.entity
        self.builder.add_relation(
            parent,
            RelationPredicate.DEFINES,
            entity,
            Polarity.positive,
            {"scope": self.frame.qualname},
            self.provenance,
            [evidence],
        )
        self.builder.add_relation(
            parent,
            RelationPredicate.CONTAINS,
            entity,
            Polarity.positive,
            {"scope": self.frame.qualname},
            self.provenance,
            [evidence],
        )
        self.symbols_by_scope.setdefault(self.frame.qualname, {})[node.name] = entity
        if self.frame.class_entity is not None:
            self.class_members.setdefault(self.frame.class_entity.id, {})[node.name] = entity
        if kind == EntityKind.class_:
            self.class_members.setdefault(entity.id, {})
        return entity

    def resolve_name(self, name: str) -> Entity | None:
        for frame in reversed(self.frames):
            if self._name_is_ambiguous(frame.qualname, name):
                return None
            local = self.symbols_by_scope.get(frame.qualname, {}).get(name)
            if local is not None:
                return local
        return None

    def resolve_import(self, name: str) -> _ImportBinding | None:
        for frame in reversed(self.frames):
            if self._name_is_ambiguous(frame.qualname, name):
                return None
            imported = self.imports_by_scope.get(frame.qualname, {}).get(name)
            if imported is not None:
                return imported
            if self.symbols_by_scope.get(frame.qualname, {}).get(name) is not None:
                return None
        return None

    def _name_is_ambiguous(self, scope: str, name: str) -> bool:
        if scope in self.star_import_scopes:
            return True
        is_shadowed = name in self.shadowed_by_scope.get(scope, set())
        is_both_imported_and_defined = (
            name in self.symbols_by_scope.get(scope, {})
            and name in self.imports_by_scope.get(scope, {})
        )
        return is_shadowed or is_both_imported_and_defined

    def external_reference(
        self,
        qualified_name: str,
        kind: EntityKind,
        evidence: Any,
        surface_form: str,
    ) -> Entity:
        entity = self.builder.add_entity(
            kind,
            qualified_name,
            _namespace(self.document.source_id),
            _identity_key(
                self.document.source_id,
                self.document.relative_path,
                f"external:{qualified_name}",
                kind,
            ),
            aliases=[surface_form],
        )
        self.builder.add_mention(entity, evidence, surface_form)
        return entity

    def expression_target(self, expression: ast.expr, *, expected: EntityKind) -> Entity | None:
        if isinstance(expression, ast.Name):
            target = self.resolve_name(expression.id)
            if target is not None:
                return target
            imported = self.resolve_import(expression.id)
            if imported is not None:
                if self.source_index is not None and not imported.resolvable:
                    return None
                indexed = self._resolve_indexed_import(
                    imported.qualified_prefix, expected=expected
                )
                if self.source_index is not None:
                    return indexed
                if expected == EntityKind.module:
                    return imported.entity
                evidence = self._expression_evidence(expression)
                if evidence is None:
                    return None
                return self.external_reference(
                    imported.qualified_prefix,
                    expected,
                    evidence,
                    expression.id,
                )
            return None
        if isinstance(expression, ast.Attribute):
            chain = self._attribute_chain(expression)
            if chain is None:
                return None
            root, suffix = chain
            if self.frame.class_entity is not None and root in {"self", "cls"}:
                return self.class_members.get(self.frame.class_entity.id, {}).get(suffix)
            local = self.resolve_name(root)
            if local is not None and local.kind == EntityKind.class_:
                return self.class_members.get(local.id, {}).get(suffix)
            imported = self.resolve_import(root)
            if imported is not None:
                if self.source_index is not None and not imported.resolvable:
                    return None
                qualified = ".".join(part for part in [imported.qualified_prefix, suffix] if part)
                indexed = self._resolve_indexed_import(qualified, expected=expected)
                if self.source_index is not None:
                    return indexed
                if not imported.is_module:
                    return None
                evidence = self._expression_evidence(expression)
                if evidence is None:
                    return None
                surface = self._source_text(expression)
                return self.external_reference(qualified, expected, evidence, surface)
        return None

    def _resolve_indexed_import(
        self, qualified_name: str, *, expected: EntityKind
    ) -> Entity | None:
        if self.source_index is None or self.builder.corpus_id is None:
            return None
        indexed = self.source_index.resolve(
            qualified_name,
            expected=expected,
            source_id=self.document.source_id,
            source_version_id=self.source_version_id,
            corpus_id=self.builder.corpus_id,
        )
        if indexed is None:
            return None
        return self.builder.add_entity(
            indexed.kind,
            indexed.canonical_name,
            indexed.namespace,
            indexed.identity_key,
            indexed.aliases,
        )

    def _attribute_chain(self, expression: ast.Attribute) -> tuple[str, str] | None:
        parts = [expression.attr]
        value: ast.expr = expression.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if not isinstance(value, ast.Name):
            return None
        parts.reverse()
        return value.id, ".".join(parts)

    def _source_text(self, node: ast.AST) -> str:
        segment = ast.get_source_segment(self.text, node)
        return segment if segment is not None else ""

    def _expression_evidence(self, node: ast.AST) -> Any:
        source_range = _node_range(node, self.text, self.line_offsets)
        if source_range is None:
            return None
        return self.builder.evidence_at_doc_range(*source_range, self.provenance)


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, analyzer: _StaticExtractor) -> None:
        self.analyzer = analyzer
        self.frames = analyzer.frames

    @property
    def frame(self) -> _Frame:
        return self.frames[-1]

    def _walk_function_outer(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for value in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            self.visit(node.args.kwarg.annotation)
        if node.returns:
            self.visit(node.returns)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        entity = self.analyzer.symbols_by_scope.get(self.frame.qualname, {}).get(node.name)
        self._walk_function_outer(node)
        if entity is None:
            return
        class_entity = self.frame.class_entity
        self.frames.append(_Frame(entity.canonical_name, entity, class_entity))
        for statement in node.body:
            self.visit(statement)
        self.frames.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for value in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(value)
        entity = self.analyzer.symbols_by_scope.get(self.frame.qualname, {}).get(node.name)
        if entity is None:
            return
        self.frames.append(_Frame(entity.canonical_name, entity, entity))
        for statement in node.body:
            self.visit(statement)
        self.frames.pop()

    def visit_Import(self, node: ast.Import) -> None:
        source_range = _node_range(node, self.analyzer.text, self.analyzer.line_offsets)
        if source_range is None:
            return
        evidence = self.analyzer.builder.evidence_at_doc_range(*source_range, self.analyzer.provenance)
        if evidence is None:
            self.analyzer.builder.reject(
                "SOURCE_SPAN_UNAVAILABLE",
                f"No input chunk span covers an import statement at line {node.lineno}.",
            )
            return
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            target = self._import_entity(alias.name, local_name, evidence)
            prefix = alias.name if alias.asname else local_name
            self._bind(local_name, _ImportBinding(target, prefix, True))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        source_range = _node_range(node, self.analyzer.text, self.analyzer.line_offsets)
        if source_range is None:
            return
        evidence = self.analyzer.builder.evidence_at_doc_range(*source_range, self.analyzer.provenance)
        if evidence is None:
            self.analyzer.builder.reject(
                "SOURCE_SPAN_UNAVAILABLE",
                f"No input chunk span covers an import statement at line {node.lineno}.",
            )
            return
        resolved_module = _resolve_import_module(
            relative_path=self.analyzer.document.relative_path,
            level=node.level,
            imported_module=node.module,
        )
        module = resolved_module or ""
        for alias in node.names:
            if alias.name == "*":
                self.analyzer.builder.reject(
                    "STAR_IMPORT_UNRESOLVED",
                    f"Star import target is unresolved at line {node.lineno}.",
                )
                self.analyzer.star_import_scopes.add(self.frame.qualname)
                continue
            local_name = alias.asname or alias.name
            qualified = ".".join(part for part in [module, alias.name] if part)
            target = self._import_entity(qualified, local_name, evidence)
            self._bind(
                local_name,
                _ImportBinding(
                    target,
                    qualified,
                    False,
                    resolvable=resolved_module is not None,
                ),
            )

    def _import_entity(self, qualified_name: str, local_name: str, evidence: Any) -> Entity:
        entity = self.analyzer.external_reference(
            qualified_name,
            EntityKind.module,
            evidence,
            local_name,
        )
        self.analyzer.builder.add_relation(
            self.analyzer.module_entity,
            RelationPredicate.IMPORTS,
            entity,
            Polarity.positive,
            {"alias": local_name},
            self.analyzer.provenance,
            [evidence],
        )
        return entity

    def _bind(self, local_name: str, binding: _ImportBinding) -> None:
        scope_imports = self.analyzer.imports_by_scope.setdefault(self.frame.qualname, {})
        if local_name in scope_imports:
            self.analyzer.shadowed_by_scope.setdefault(self.frame.qualname, set()).add(
                local_name
            )
            return
        scope_imports[local_name] = binding


class _BindingVisitor(ast.NodeVisitor):
    """Track local writes so reassigned names are never treated as static call targets."""

    def __init__(self, analyzer: _StaticExtractor) -> None:
        self.analyzer = analyzer
        self.frames = analyzer.frames

    @property
    def frame(self) -> _Frame:
        return self.frames[-1]

    def _bind_target(self, node: ast.AST) -> None:
        names: set[str] = set()
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                if isinstance(child, ast.Name):
                    names.add(child.id)
        self.analyzer.shadowed_by_scope.setdefault(self.frame.qualname, set()).update(names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for value in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)
        entity = self.analyzer.symbols_by_scope.get(self.frame.qualname, {}).get(node.name)
        if entity is None:
            return
        self.frames.append(_Frame(entity.canonical_name, entity, self.frame.class_entity))
        scope = self.frame.qualname
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        self.analyzer.shadowed_by_scope.setdefault(scope, set()).update(
            argument.arg for argument in arguments
        )
        if node.args.vararg is not None:
            self.analyzer.shadowed_by_scope[scope].add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            self.analyzer.shadowed_by_scope[scope].add(node.args.kwarg.arg)
        for statement in node.body:
            self.visit(statement)
        self.frames.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for value in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(value)
        entity = self.analyzer.symbols_by_scope.get(self.frame.qualname, {}).get(node.name)
        if entity is None:
            return
        self.frames.append(_Frame(entity.canonical_name, entity, entity))
        for statement in node.body:
            self.visit(statement)
        self.frames.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._bind_target(node.target)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.analyzer.shadowed_by_scope.setdefault(self.frame.qualname, set()).add(node.name)
        self.generic_visit(node)


class _CallAndInheritanceVisitor(ast.NodeVisitor):
    def __init__(self, analyzer: _StaticExtractor) -> None:
        self.analyzer = analyzer
        self.frames = analyzer.frames
        self.guards: list[tuple[dict[str, str], ast.expr]] = []
        self.function_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    @property
    def frame(self) -> _Frame:
        return self.frames[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for value in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)
        entity = self.analyzer.symbols_by_scope.get(self.frame.qualname, {}).get(node.name)
        if entity is None:
            for statement in node.body:
                self.visit(statement)
            return
        class_entity = self.frame.class_entity
        self.frames.append(_Frame(entity.canonical_name, entity, class_entity))
        self.function_nodes.append(node)
        for statement in node.body:
            self.visit(statement)
        self.function_nodes.pop()
        self.frames.pop()

    def _with_enclosing_function_evidence(self, evidence_items: list[Any]) -> list[Any]:
        if not self.function_nodes:
            return evidence_items
        function_node = self.function_nodes[-1]
        function_range = _node_range(
            function_node, self.analyzer.text, self.analyzer.line_offsets
        )
        if function_range is None:
            return evidence_items
        function_evidence = self.analyzer.builder.evidence_at_doc_range(
            *function_range, self.analyzer.provenance
        )
        if function_evidence is None or any(
            item.id == function_evidence.id for item in evidence_items
        ):
            return evidence_items
        return [*evidence_items, function_evidence]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_entity = self.analyzer.symbols_by_scope.get(self.frame.qualname, {}).get(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        if class_entity is not None:
            self._inheritance_relations(node, class_entity)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        if class_entity is None:
            for statement in node.body:
                self.visit(statement)
            return
        self.frames.append(_Frame(class_entity.canonical_name, class_entity, class_entity))
        for statement in node.body:
            self.visit(statement)
        self.frames.pop()

    def _inheritance_relations(self, node: ast.ClassDef, subject: Entity) -> None:
        for base in node.bases:
            if not isinstance(base, (ast.Name, ast.Attribute)):
                self.analyzer.builder.reject(
                    "DYNAMIC_INHERITANCE_TARGET_UNRESOLVED",
                    f"Dynamic inheritance target omitted at line {getattr(base, 'lineno', node.lineno)}.",
                )
                continue
            target = self.analyzer.expression_target(base, expected=EntityKind.class_)
            evidence = self.analyzer._expression_evidence(base)
            if target is None or evidence is None:
                reason = "Static inheritance target could not be resolved" if target is None else "No input chunk span covers the inheritance target"
                self.analyzer.builder.reject(
                    "INHERITANCE_TARGET_UNRESOLVED",
                    f"{reason} at line {getattr(base, 'lineno', node.lineno)}.",
                )
                continue
            self.analyzer.builder.add_mention(
                target, evidence, ast.get_source_segment(self.analyzer.text, base) or target.canonical_name
            )
            self.analyzer.builder.add_relation(
                subject,
                RelationPredicate.INHERITS,
                target,
                Polarity.positive,
                {"resolution": "static_expression"},
                self.analyzer.provenance,
                [evidence],
            )

    def _outcome_entity(
        self,
        expression: ast.expr | None,
        *,
        label: str,
        kind: EntityKind,
        evidence: Any,
        mention_surface: str | None = None,
    ) -> Entity:
        if expression is not None:
            surface = self.analyzer._source_text(expression)
        elif label == "return-value":
            surface = "None"
        else:
            surface = label
        canonical_name = surface.strip() or label
        identity = _identity_key(
            self.analyzer.document.source_id,
            self.analyzer.document.relative_path,
            f"{self.frame.qualname}::{label}:{canonical_name}",
            kind,
        )
        entity = self.analyzer.builder.add_entity(
            kind,
            canonical_name,
            _namespace(self.analyzer.document.source_id),
            identity,
        )
        self.analyzer.builder.add_mention(
            entity,
            evidence,
            mention_surface or surface or "return",
        )
        return entity

    def _outcome_evidence(self, node: ast.Return | ast.Raise) -> Any | None:
        source_range = _node_range(node, self.analyzer.text, self.analyzer.line_offsets)
        if source_range is None:
            return None
        return self.analyzer.builder.evidence_at_doc_range(
            *source_range, self.analyzer.provenance
        )

    def _add_outcome(
        self,
        node: ast.Return | ast.Raise,
        *,
        predicate: RelationPredicate,
        expression: ast.expr | None,
        object_kind: EntityKind,
        label: str,
        outcome_kind: str,
        result_role: str,
    ) -> None:
        evidence = self._outcome_evidence(node)
        if evidence is None:
            self.analyzer.builder.reject(
                "OUTCOME_SOURCE_SPAN_UNAVAILABLE",
                f"No source-backed span covers a {predicate.value} outcome at line {node.lineno}.",
            )
            return
        target = self._outcome_entity(
            expression,
            label=label,
            kind=object_kind,
            evidence=evidence,
            mention_surface=(
                "return"
                if expression is None and predicate is RelationPredicate.RETURNS
                else None
            ),
        )
        relation_evidence = self._with_enclosing_function_evidence([evidence])
        for _guard, guard_expression in self.guards:
            guard_evidence = self.analyzer._expression_evidence(guard_expression)
            if guard_evidence is not None:
                relation_evidence.append(guard_evidence)
        qualifiers: dict[str, Any] = {}
        if self.guards:
            qualifiers["guard"] = [item for item, _expression in self.guards]
        qualifiers["outcome_kind"] = outcome_kind
        qualifiers["result_role"] = result_role
        self.analyzer.builder.add_relation(
            self.frame.entity,
            predicate,
            target,
            Polarity.positive,
            qualifiers,
            self.analyzer.provenance,
            relation_evidence,
        )

    def visit_If(self, node: ast.If) -> None:
        condition = self.analyzer._source_text(node.test).strip()
        self.visit(node.test)
        self.guards.append(({"condition": condition, "branch": "true"}, node.test))
        for statement in node.body:
            self.visit(statement)
        self.guards.pop()
        if node.orelse:
            self.guards.append(({"condition": condition, "branch": "false"}, node.test))
            for statement in node.orelse:
                self.visit(statement)
            self.guards.pop()

    def visit_Return(self, node: ast.Return) -> None:
        expression = node.value
        self._add_outcome(
            node,
            predicate=RelationPredicate.RETURNS,
            expression=expression,
            object_kind=EntityKind.concept,
            label="return-value",
            outcome_kind="RETURN_VALUE",
            result_role="return_value",
        )
        if expression is not None:
            self.visit(expression)

    def visit_Raise(self, node: ast.Raise) -> None:
        expression = node.exc
        if expression is None:
            self.analyzer.builder.reject(
                "RERAISE_TARGET_UNRESOLVED",
                f"Bare re-raise target is context-dependent at line {node.lineno}; no RAISES edge was emitted.",
            )
            return
        raised_type = expression.func if isinstance(expression, ast.Call) else expression
        self._add_outcome(
            node,
            predicate=RelationPredicate.RAISES,
            expression=raised_type,
            object_kind=EntityKind.class_ if isinstance(expression, ast.Call) else EntityKind.concept,
            label="raised-exception",
            outcome_kind="EXCEPTION",
            result_role="exception",
        )
        self.visit(expression)

    def _assignment_evidence(self, node: ast.AST) -> Any | None:
        source_range = _node_range(node, self.analyzer.text, self.analyzer.line_offsets)
        if source_range is None:
            return None
        return self.analyzer.builder.evidence_at_doc_range(
            *source_range, self.analyzer.provenance
        )

    def _emit_state_update(self, node: ast.AST, target: ast.expr, value: ast.expr) -> None:
        if self.frame.entity.kind is not EntityKind.function:
            return
        if self.frame.qualname.rsplit(".", 1)[-1] == "__init__":
            return
        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
            return
        if target.value.id not in {"self", "cls"}:
            return
        evidence = self._assignment_evidence(node)
        if evidence is None:
            self.analyzer.builder.reject(
                "STATE_UPDATE_SOURCE_SPAN_UNAVAILABLE",
                f"No source-backed span covers a state assignment at line {getattr(node, 'lineno', 1)}.",
            )
            return
        state = self._outcome_entity(
            target,
            label="state-target",
            kind=EntityKind.concept,
            evidence=evidence,
        )
        operation = (
            f"AUGMENTED_{type(node.op).__name__.upper()}"
            if isinstance(node, ast.AugAssign)
            else "ASSIGN"
        )
        qualifiers: dict[str, Any] = {
            "operation": operation,
            "written_value": self.analyzer._source_text(value).strip(),
        }
        relation_evidence = self._with_enclosing_function_evidence([evidence])
        if self.guards:
            qualifiers["guard"] = [item for item, _expression in self.guards]
            for _guard, guard_expression in self.guards:
                guard_evidence = self.analyzer._expression_evidence(guard_expression)
                if guard_evidence is not None:
                    relation_evidence.append(guard_evidence)
        self.analyzer.builder.add_relation(
            self.frame.entity,
            RelationPredicate.UPDATES,
            state,
            Polarity.positive,
            qualifiers,
            self.analyzer.provenance,
            relation_evidence,
        )

    def _emit_literal_mapping(self, node: ast.AST, target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Dict):
            return
        evidence = self._assignment_evidence(node)
        if evidence is None:
            return
        mapping_entity = self._outcome_entity(
            target,
            label="mapping-table",
            kind=EntityKind.concept,
            evidence=evidence,
        )
        for key_node, value_node in zip(value.keys, value.values, strict=True):
            if key_node is None or not isinstance(key_node, ast.Constant):
                continue
            if not isinstance(key_node.value, (str, int, bool)):
                continue
            if not isinstance(value_node, (ast.Constant, ast.Name, ast.Attribute)):
                continue
            target_entity = self._outcome_entity(
                value_node,
                label="mapping-value",
                kind=EntityKind.concept,
                evidence=evidence,
            )
            self.analyzer.builder.add_relation(
                mapping_entity,
                RelationPredicate.MAPS_TO,
                target_entity,
                Polarity.positive,
                {"key": key_node.value},
                self.analyzer.provenance,
                [evidence],
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._emit_state_update(node, target, node.value)
            self._emit_literal_mapping(node, target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._emit_state_update(node, node.target, node.value)
            self._emit_literal_mapping(node, node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._emit_state_update(node, node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        evidence = self.analyzer._expression_evidence(node)
        target: Entity | None = None
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            target = self.analyzer.expression_target(node.func, expected=EntityKind.function)
        if target is None or evidence is None:
            line = getattr(node, "lineno", 1)
            dynamic = not isinstance(node.func, (ast.Name, ast.Attribute))
            code = "DYNAMIC_CALL_TARGET_UNRESOLVED" if dynamic else "CALL_TARGET_UNRESOLVED"
            detail = "Dynamic" if dynamic else "Static"
            self.analyzer.builder.reject(
                code,
                f"{detail} call target could not be resolved at line {line}; no CALLS relation was emitted.",
            )
        else:
            surface = ast.get_source_segment(self.analyzer.text, node.func) or target.canonical_name
            self.analyzer.builder.add_mention(target, evidence, surface)
            relation_evidence = [evidence]
            qualifiers: dict[str, Any] = {"resolution": "static_symbol"}
            if self.guards:
                qualifiers["guard"] = [item for item, _expression in self.guards]
                for _guard, guard_expression in self.guards:
                    guard_evidence = self.analyzer._expression_evidence(guard_expression)
                    if guard_evidence is not None:
                        relation_evidence.append(guard_evidence)
            relation_evidence = self._with_enclosing_function_evidence(
                relation_evidence
            )
            self.analyzer.builder.add_relation(
                self.frame.entity,
                RelationPredicate.CALLS,
                target,
                Polarity.positive,
                qualifiers,
                self.analyzer.provenance,
                relation_evidence,
            )
        self.generic_visit(node)


def extract_static_python(
    request: ExtractionRequest,
    *,
    corpus_id: str | None,
    source_index: _PythonSourceIndex | None = None,
    source_version_id: str | None = None,
) -> ExtractionResult:
    builder = ExtractionBuilder(request, corpus_id=corpus_id)
    if corpus_id is None:
        builder.reject(
            "CORPUS_SCOPE_UNAVAILABLE",
            "A corpus_id mapping is required to create entity identities; no entities were emitted.",
        )
        return builder.result()

    document = request.document
    producer = builder.add_provenance(ProvenanceMethod.STATIC_ANALYSIS)
    try:
        tree = ast.parse(document.normalized_text, filename=document.relative_path)
    except (SyntaxError, ValueError, TypeError) as exc:
        line = getattr(exc, "lineno", None)
        location = f" at line {line}" if isinstance(line, int) else ""
        builder.reject(
            "AST_PARSE_FAILED",
            f"Python source could not be parsed{location}; no static relations were emitted.",
        )
        return builder.result()

    module_name = _module_name(document.relative_path)
    module_identity = _identity_key(
        document.source_id,
        document.relative_path,
        module_name,
        EntityKind.module,
    )
    module_entity: Entity | None = None
    for index, character in enumerate(document.normalized_text):
        if not character.isspace():
            module_evidence = builder.evidence_at_doc_range(index, index + 1, producer)
            if module_evidence is not None:
                module_entity = builder.add_entity(
                    EntityKind.module,
                    module_name,
                    _namespace(document.source_id),
                    module_identity,
                )
                builder.add_mention(module_entity, module_evidence, document.relative_path)
            break
    if module_entity is None:
        builder.reject(
            "SOURCE_SPAN_UNAVAILABLE",
            "No source-backed Python text is available for the module entity.",
        )
        return builder.result()

    analyzer = _StaticExtractor(
        request,
        builder,
        module_entity,
        producer,
        source_index,
        source_version_id,
    )
    analyzer.visit(tree)
    import_visitor = _ImportVisitor(analyzer)
    import_visitor.visit(tree)
    binding_visitor = _BindingVisitor(analyzer)
    binding_visitor.visit(tree)
    resolution_visitor = _CallAndInheritanceVisitor(analyzer)
    resolution_visitor.visit(tree)
    return builder.result()
