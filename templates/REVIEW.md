# 架构/里程碑审查模板

review_id / reviewer / timestamp：
type：architecture / milestone / release
plan_version / plan_hash：
integration_commit（Phase0无Git可空）：
审查范围与证据：
结论：approved / changes_requested / blocked
限制、必须修复项与批准的授权范围：

Phase0由用户或其指定审查者确认，不能由Orchestrator自签。里程碑审查绑定具体SHA与plan_hash。批准记录放state/planning/reviews/或执行时runtime；向Git提交审查记录不能替代对所列SHA的批准。
