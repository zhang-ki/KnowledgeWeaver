from knowledge_weaver.storage.schema import CorpusRow, GenerationRow


def test_active_generation_constraint_is_same_corpus_and_nullable() -> None:
    foreign_key = next(iter(CorpusRow.__table__.foreign_key_constraints))
    assert foreign_key.name == "fk_corpora_active_generation_same_corpus"
    assert [column.name for column in foreign_key.columns] == ["id", "active_generation_id"]
    assert [element.target_fullname for element in foreign_key.elements] == ["generations.corpus_id", "generations.id"]
    assert foreign_key.deferrable is True
    assert foreign_key.initially == "DEFERRED"
    assert CorpusRow.__table__.c.active_generation_id.nullable is True

    unique = next(constraint for constraint in GenerationRow.__table__.constraints if constraint.name == "uq_generation_corpus_id")
    assert [column.name for column in unique.columns] == ["corpus_id", "id"]
