# Hermes local patch manifest

Upstream base: `d83644895181dffc82ba7924b8a04dfe32376eb4`.
Live predecessor: `614789da47a94702e32475741497e087ad70a9e9`.
Delivery branch: `candidate/update-20261116`, replayed on 2026-11-16 from the live first-parent history.
Kept topics: **32** numbered, plus the requested additions and the 2026-10-30/31 patches, from **56** source commits and **2** adaptation commits.
Use this manifest instead of searching historical `LOCAL-PATCH` subjects.
Every replayed commit keeps the original author metadata and gains a `(cherry picked from ...)` footer naming its live-branch source.
The swap merge makes upstream and the live predecessor both ancestors.

The shared compatibility commit is `19b6a446a9c452287c9c0047589248eb0c2714b9`.
It moves code into topic modules and uses the upstream config reader.
The adaptation commits are `a070247e1fbbce9576a99dc1285c1bb4ff455ede`, `e286559f16aaade3a8608a60746ed3ef62add52e`.

Replace later, once the hermes-local plugin carries the job: topics 1, 10, 15, 16, 20, 21, 22, 29, 35.
They stay in this replay so behavior does not change.

## 1. background-review-prompt-hook

Commits: `8984e7676fd60798895f964b9b386d0c0cd63ac0`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Replace the review request through a plugin hook.

Files:

- `agent/background_review.py`
- `hermes_cli/plugins.py`
- `hermes_cli/plugins_directive_results.py`
- `tests/agent/test_background_review_prompt_hook.py`
- `website/docs/developer-guide/plugins/index.md`

Proof:

- `tests/agent/test_background_review_prompt_hook.py::test_hook_is_registered`
- `tests/agent/test_background_review_prompt_hook.py::test_hook_replaces_the_prompt_and_focus_still_appends`
- `tests/agent/test_background_review_prompt_hook.py::test_no_hook_result_keeps_the_default_prompt`

## 2. kanban-incremental-read

Commits: `a7a7c7ecbb0f360e29cdc253f8a2c96fa9afae63`.

Read bounded card state or changes after a cursor.

Files:

- `tests/tools/test_kanban_incremental_read.py`
- `tools/kanban_task_updates.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

Proof:

- `tests/tools/test_kanban_incremental_read.py::test_concurrent_comment_is_delivered_on_the_next_snapshot`
- `tests/tools/test_kanban_incremental_read.py::test_cursor_pages_without_skipping_or_accepting_another_task`
- `tests/tools/test_kanban_incremental_read.py::test_cursor_returns_a_revised_brief_in_full`
- `tests/tools/test_kanban_incremental_read.py::test_orientation_read_keeps_only_the_latest_comments_and_runs`
- `tests/tools/test_kanban_incremental_read.py::test_orientation_read_without_overflow_has_no_note`
- `tests/tools/test_kanban_incremental_read.py::test_task_cursor_keeps_same_second_decisions_without_repeating_brief`

## 3. kanban-merged-no-required-checks

Commits: `a43ad5e30867d868332b7a7cfb9ab18681c17500`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Accept a merged PR when no checks are required.

Files:

- `hermes_cli/kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py`

Proof:

- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_merged_pr_with_required_checks_still_needs_them`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_merged_pr_without_required_checks_is_accepted`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_open_pr_without_required_checks_is_refused`

## 4. kanban-dispatch-task

Commits: `52337c35071b3e0866c3b9312daf346ce829340a`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Dispatch one named card through the normal gates.

Files:

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `hermes_cli/kanban_ops.py`
- `tests/hermes_cli/test_kanban_dispatch_task.py`
- `tools/kanban_tools.py`

Proof:

- `tests/hermes_cli/test_kanban_dispatch_task.py::test_dispatch_task_keeps_guards_caps_and_board_lock`
- `tests/hermes_cli/test_kanban_dispatch_task.py::test_dispatch_task_respects_dependencies`
- `tests/hermes_cli/test_kanban_dispatch_task.py::test_dispatch_task_spawns_exactly_the_named_card_once`
- `tests/hermes_cli/test_kanban_dispatch_task.py::test_spawn_from_inside_a_worker_does_not_leak_its_identity`

## 6. kanban-authorized-pr-resume

Commits: `b4fd6b1b6f164e22d14c6bc5d8ce148d689d4cc3`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Resume held PR work with a recorded operator authorization.

Files:

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `hermes_cli/kanban_pr_resume.py`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `toolsets.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_authorization_order_and_same_second_ties`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_authorization_refuses_untrusted_comments`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_authorized_draft_resume_is_explicit_and_bound`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_changed_snapshots_invalidate_coverage`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_closed_reference_may_coexist_and_new_pr_comment_rearms`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_github_evidence_fails_closed`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_historical_fingerprint_alone_is_not_ownership`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_historical_fingerprint_network_race_refuses`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_historical_fingerprint_receipt_invalidates_on_change`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_historical_fingerprint_with_ownership_or_ambiguity_refuses`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_legacy_operator_label_remains_usable_but_provenance_changes_invalidate`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_malformed_recovery_record_never_lifts_or_crashes_guard`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_network_race_rechecks_real_sqlite_snapshot`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_new_authorization_records_its_own_id_and_own_card_api_refuses`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_not_completely_unowned_refuses_before_github`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_open_pr_must_match_repository_or_exact_contract`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_ownership_round_trip_invalidates_old_authorization`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_same_owner_noop_assignment_does_not_clear_hold_or_invalidate_resume`
- `tests/hermes_cli/test_kanban_authorized_pr_resume.py::test_unknown_comment_origin_fails_closed`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_concurrent_recovery_and_dispatch_spawn_only_target_once`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_default_profile_worker_cannot_claim_user_authorization`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_historical_fingerprint_concurrent_dispatch_claims_once`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_historical_fingerprint_keeps_worker_origin_refusal`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_historical_fingerprint_resumes_and_spawns_once`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_invalid_operator_arguments_change_nothing`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_own_card_and_owned_target_refuse`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_resume_preserves_other_respawn_guards`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_shared_board_keeps_profile_admission_across_a_b_a`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_targeted_spawn_keeps_dispatch_admission_checks`
- `tests/tools/test_kanban_authorized_pr_resume_tool.py::test_worker_surface_and_dry_run_then_idempotent_record`

## 7. kanban-guard-event-dedupe

Commits: `0a6a4a5225809e2bfde70d05bc260befded99eee`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Record a repeated dispatcher hold once.

Files:

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `tests/hermes_cli/test_kanban_dispatch_task.py`

Proof:

- `tests/hermes_cli/test_kanban_dispatch_task.py::test_dispatch_task_respects_dependencies`
- `tests/hermes_cli/test_kanban_dispatch_task.py::test_held_card_records_its_guard_once_per_hold_not_per_tick`

## 8. kanban-pr-state-guard

Commits: `d2771640763bad896553100303588b83da8c2e37`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Let only an open PR hold a ready card.

Files:

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `hermes_cli/kanban_pr_acceptance.py`
- `tests/conftest.py`
- `tests/hermes_cli/test_kanban_pr_state_guard.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_failed_lookup_keeps_the_hold_as_pr_state_unknown`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_github_lookup_reads_state_with_a_short_timeout`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_lookups_are_cached_per_url`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_manual_reclaim_after_own_worker_open_pr_respawns`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_merged_and_closed_prs_never_hold_and_the_dispatcher_spawns`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_open_pr_holds_with_the_open_url_recorded`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_reclaim_exception_is_narrow`

## 9. kanban-needs-input-no-loop

Commits: `b187b831dc89455a2e7ae7cd4af53a8264116088`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Keep a new human question out of the block loop breaker.

Files:

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_task_skills.py`
- `tests/hermes_cli/test_kanban_block_kinds.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_block_kinds.py::test_needs_input_does_not_shield_a_capability_loop`
- `tests/hermes_cli/test_kanban_block_kinds.py::test_needs_input_waits_on_a_person_never_route_to_triage`

## 10. media-drop-notice

Commits: `292a2f7b349d1b093779a38551da8ec775753bfd`.

Tell the user and agent when attachment policy drops a file.

Files:

- `gateway/run_agent_cache.py`
- `gateway/run_turn_persistence.py`
- `gateway/session_state.py`
- `tests/gateway/test_media_drop_notice.py`

Proof:

- `tests/gateway/test_media_drop_notice.py::test_allowed_attachment_adds_nothing`
- `tests/gateway/test_media_drop_notice.py::test_dropped_attachment_is_named_to_the_user_and_the_next_turn`
- `tests/gateway/test_media_drop_notice.py::test_next_turn_notes_keep_the_carried_note`
- `tests/gateway/test_media_drop_notice.py::test_streamed_reply_gets_the_notice_as_a_trailing_message`

## 11. compression-summary-facts

Commits: `46b92c2fe8042205f4d5fc5a29dc3c46328c47dd`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Keep recent tool facts, language, and unanswered questions in summaries.

Files:

- `agent/context_compressor.py`
- `agent/context_compressor_facts.py`
- `agent/context_compressor_summary.py`
- `hermes_cli/config_defaults.py`
- `scripts/ci/docs_drift_baseline.json`
- `tests/agent/test_compression_summary_facts.py`

Proof:

- `tests/agent/test_compression_summary_facts.py::test_a_later_compaction_replaces_the_facts_section`
- `tests/agent/test_compression_summary_facts.py::test_a_pruned_tool_result_keeps_its_facts`
- `tests/agent/test_compression_summary_facts.py::test_a_question_answered_in_the_kept_tail_is_labelled_answered`
- `tests/agent/test_compression_summary_facts.py::test_clarify_answers_are_never_lifted_as_facts`
- `tests/agent/test_compression_summary_facts.py::test_configured_language_replaces_the_follow_the_user_rule`
- `tests/agent/test_compression_summary_facts.py::test_facts_from_the_elided_middle_reach_the_prompt_and_the_summary`
- `tests/agent/test_compression_summary_facts.py::test_facts_survive_the_pre_compression_prune`
- `tests/agent/test_compression_summary_facts.py::test_plain_prose_is_not_a_fact`
- `tests/agent/test_compression_summary_facts.py::test_summary_language_is_a_known_config_key`
- `tests/agent/test_compression_summary_facts.py::test_unanswered_question_is_labelled_and_answered_one_is_not`

## 12. kanban-show-reader-context

Commits: `7fcee29b75e1abf886e30ea76cb97679703bf385`.

Give worker context only to that card's own worker.

Files:

- `tests/tools/test_kanban_incremental_read.py`
- `tools/kanban_task_updates.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

Proof:

- `tests/tools/test_kanban_incremental_read.py::test_reader_that_is_not_the_task_worker_gets_fields_without_worker_context`

## 13. kanban-plan-restricted-rules

Commits: `e414c7464d20f0680323d32496e4237018ff019a`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Accept merged private PRs when GitHub hides branch rules.

Files:

- `hermes_cli/kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py`

Proof:

- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_api_tells_plan_restriction_from_auth`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_merged_pr_with_a_failed_check_is_refused`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_merged_pr_with_a_failed_legacy_status_is_refused`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_merged_pr_with_a_running_check_waits`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_merged_pr_with_passing_checks_is_accepted`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_open_pr_is_still_refused`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_other_403_on_rules_is_still_auth`

## 14. kanban-github-read-scope

Commits: `e571a35768d99cf7d80a630f59fc13fb5baca7ec`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Route repository reads through its configured GitHub login.

Files:

- `hermes_cli/kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_github_read_transport.py`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_github_read_transport.py::test_assignee_reads_use_only_that_profile_config`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_completion_acceptance_reads_every_endpoint_through_the_route`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_config_that_cannot_prove_the_route_absent_fails_closed`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_declared_graphql_repository_mismatch_fails_consistently`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_exact_repo_route_reads_on_the_remote_login_only`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_extra_open_sibling_is_refused`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_graphql_read_is_routed_with_its_explicit_repository`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_graphql_without_repo_keeps_the_historical_local_command`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_malformed_matching_entry_fails_closed`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_malformed_matching_route_fails_closed`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_missing_file_absent_key_or_unmatched_repo_keeps_the_local_login`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_missing_remote_token_never_falls_back`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_no_route_keeps_the_local_command_byte_for_byte`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_open_sibling_guard_still_refuses_contract_mismatch`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_routed_graphql_cannot_paginate`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_routed_graphql_must_be_the_exact_acceptance_query`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_routed_reads_are_limited_to_the_acceptance_endpoints`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_sibling_and_prefix_repositories_are_not_routed`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_ssh_failure_and_remote_denial_never_fall_back`
- `tests/hermes_cli/test_kanban_github_read_transport.py::test_true_contract_pr_open_verifies_dry_run`

## 15. kanban-quiet-notices

Commits: `8962f71f7d2ac7e0b336dbc7263b1be46dc87e53`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Wake the chat agent without posting raw board notices.

Files:

- `gateway/kanban_watchers_notifier.py`
- `gateway/slash_commands.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_db_notify.py`
- `scripts/ci/docs_drift_baseline.json`
- `tests/gateway/test_kanban_quiet_notices.py`
- `tools/kanban_tools.py`

Proof:

- `tests/gateway/test_kanban_quiet_notices.py::test_auto_subscribe_target_uses_configured_mode`
- `tests/gateway/test_kanban_quiet_notices.py::test_chat_delivery_mode_reads_config`
- `tests/gateway/test_kanban_quiet_notices.py::test_notify_wake_still_posts_notice`
- `tests/gateway/test_kanban_quiet_notices.py::test_wake_only_posts_no_notice_but_uploads_files`
- `tests/gateway/test_kanban_quiet_notices.py::test_wake_retry_does_not_upload_files_twice`

## 16. kanban-sub-route

Commits: `2ec9a5a6602d935bb0fb052d1262cfa9e7f73d1d`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Normalize Discord subscriptions into the existing chat session.

Files:

- `hermes_cli/kanban_db_notify.py`
- `tests/hermes_cli/test_kanban_sub_route.py`

Proof:

- `tests/hermes_cli/test_kanban_sub_route.py::test_configured_wake_overrides_explicit_notify_wake`
- `tests/hermes_cli/test_kanban_sub_route.py::test_default_config_keeps_explicit_notify_wake`
- `tests/hermes_cli/test_kanban_sub_route.py::test_discord_channel_is_stored_as_group`
- `tests/hermes_cli/test_kanban_sub_route.py::test_resubscribe_does_not_downgrade_discord_group`
- `tests/hermes_cli/test_kanban_sub_route.py::test_telegram_channel_is_kept`

## 17. skill-reload-markers

Commits: `440dc808b1cdb5d3f6f67e388815d5119e01d185`, `19b6a446a9c452287c9c0047589248eb0c2714b9`, `719b4ad6fc22bc260c3df23325c27d3c2690c172`.

Remove old reload markers and reset tool guards after compaction.

Channel-bound skill bodies survive repeated compactions, including views loaded through path aliases.
Their names never receive prune markers.
Old handoff markers expire. Tool guards reset. Upstream keeps its Skill Safety Rule.

Files:

- `agent/context_compressor.py`
- `agent/context_compressor_skills.py`
- `agent/context_compressor_window.py`
- `agent/conversation_compression.py`
- `agent/conversation_compression_codex.py`
- `agent/conversation_compression_skills.py`
- `agent/tool_guardrails.py`
- `tests/agent/test_channel_bound_compaction.py`
- `tests/agent/test_ghost_skill_pruning.py`
- `tests/agent/test_guard_reset_on_compaction.py`

Proof:

- `tests/agent/test_channel_bound_compaction.py::test_bound_skill_bodies_and_path_aliases_survive_tail_demotion`
- `tests/agent/test_channel_bound_compaction.py::test_channel_bound_skills_survive_repeated_compactions_without_prune_markers`
- `tests/agent/test_compression_skill_safety_single_copy.py::test_prune_only_then_full_compaction_keeps_one_skill_safety_rule`
- `tests/agent/test_ghost_skill_pruning.py::TestMarkerSurvivesRealCompress::test_no_marker_when_skill_body_survives_in_tail`
- `tests/agent/test_ghost_skill_pruning.py::TestMarkerSurvivesRealCompress::test_old_handoff_marker_not_carried_forward`
- `tests/agent/test_guard_reset_on_compaction.py::test_compaction_restarts_identical_result_count`
- `tests/agent/test_guard_reset_on_compaction.py::test_compress_context_forgets_guard_results`
- `tests/agent/test_guard_reset_on_compaction.py::test_fifth_identical_read_blocks_within_one_context`

## 18. channel-service-tier

Commits: `6fe3af64cfb955fc25b1589b8d43f94948531b16`, `c10dbfb5a1914f386a6a341a1f65289e95ff0939`.

Apply a channel speed tier below session overrides.

Files:

- `gateway/config.py`
- `gateway/run_config_loaders.py`
- `gateway/slash_commands_model.py`
- `tests/gateway/test_channel_service_tier.py`
- `tests/gateway/test_stored_history_mount.py`
- `website/docs/user-guide/messaging/index.md`

Proof:

- `tests/gateway/test_channel_service_tier.py::test_channel_tier_applies_only_to_its_channel_and_threads`
- `tests/gateway/test_channel_service_tier.py::test_channel_ultrafast_reaches_the_turn_route_on_astra_codex`
- `tests/gateway/test_channel_service_tier.py::test_config_round_trip_keeps_service_tier_and_drops_unset`
- `tests/gateway/test_channel_service_tier.py::test_fast_status_in_the_channel_reports_the_channel_tier`
- `tests/gateway/test_channel_service_tier.py::test_session_fast_beats_channel_and_channel_beats_profile`
- `tests/gateway/test_channel_service_tier.py::test_unknown_channel_tier_warns_and_uses_profile`
- `tests/gateway/test_stored_history_mount.py::test_a_caller_that_is_not_the_owner_still_finds_nothing`
- `tests/gateway/test_stored_history_mount.py::test_a_childless_continuation_whose_chain_has_a_delegate_is_not_deleted`
- `tests/gateway/test_stored_history_mount.py::test_a_compressed_cron_history_deletes_as_one_chat`
- `tests/gateway/test_stored_history_mount.py::test_a_stored_history_with_child_sessions_is_not_deleted`
- `tests/gateway/test_stored_history_mount.py::test_an_api_chat_opened_read_only_still_continues_through_the_api`
- `tests/gateway/test_stored_history_mount.py::test_old_stored_history_opens_read_only_for_its_owner`
- `tests/gateway/test_stored_history_mount.py::test_push_to_talk_reads_voice_off_on_a_mount`
- `tests/gateway/test_stored_history_mount.py::test_session_info_answers_on_a_mount`
- `tests/gateway/test_stored_history_mount.py::test_the_owner_archives_unarchives_and_pins_old_stored_history`
- `tests/gateway/test_stored_history_mount.py::test_the_owner_deletes_a_completed_subagent_history`
- `tests/gateway/test_stored_history_mount.py::test_the_reads_a_viewer_sends_for_an_open_chat_answer_on_a_mount`

## 19. kanban-block-kinds

Commits: `8dab8dd949e913f7a67687d6e2ab03031b613857`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Report whether a blocked card waits on a person, capacity, or its stop rule.

Files:

- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban_block_kinds.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_output.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_task_skills.py`
- `tests/hermes_cli/test_kanban_block_kinds.py`
- `tools/kanban_task_updates.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_block_kinds.py::test_board_reads_tell_a_human_wait_from_capacity_stop_rule_and_dependency`
- `tests/hermes_cli/test_kanban_block_kinds.py::test_stop_rule_block_parks_the_card_and_never_counts_as_a_loop`

## 20. kanban-unblock-outcome

Commits: `632a869efd914ffd035f296472c1cab41675a1aa`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Report whether unblock actually spawns a worker.

Files:

- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_unblock_outcome.py`
- `tests/tools/test_kanban_unblock_outcome.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/tools/test_kanban_unblock_outcome.py::test_cli_unblock_prints_what_the_dispatcher_did`
- `tests/tools/test_kanban_unblock_outcome.py::test_unblock_of_a_free_card_says_no_worker_has_spawned_yet`
- `tests/tools/test_kanban_unblock_outcome.py::test_unblock_reports_the_respawn_guard_instead_of_a_bare_ready`
- `tests/tools/test_kanban_unblock_outcome.py::test_unblock_waits_for_the_dispatcher_and_reports_the_real_spawn`

## 21. kanban-wake-contract

Commits: `0e5a7b41c8b29c8d38be6a9346f963bf80d8d943`, `7c07c6dcab4f931f53a685eb44f654228e8ebe44`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Give chat wakes their cause, result, cursor, and quiet-event policy.

Files:

- `gateway/kanban_wake_contract.py`
- `gateway/kanban_watchers.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_event_origin.py`
- `tests/gateway/test_kanban_wake_contract.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/gateway/test_kanban_wake_contract.py::test_a_stop_rule_block_wakes_as_a_result_not_a_diagnostic`
- `tests/gateway/test_kanban_wake_contract.py::test_blocked_wake_names_kind_reason_cause_and_whether_a_human_is_needed`
- `tests/gateway/test_kanban_wake_contract.py::test_completed_wake_carries_the_result_up_to_the_configured_length`
- `tests/gateway/test_kanban_wake_contract.py::test_configured_drop_kinds_skip_retried_crashes_and_a_gave_up_still_wakes`
- `tests/gateway/test_kanban_wake_contract.py::test_evidence_files_stay_paths_and_deliverables_upload`
- `tests/gateway/test_kanban_wake_contract.py::test_late_crash_replays_are_dropped_and_a_late_gave_up_still_wakes`
- `tests/gateway/test_kanban_wake_contract.py::test_repeat_wakes_on_one_card_merge_inside_the_window`
- `tests/gateway/test_kanban_wake_contract.py::test_the_subscribers_own_stop_and_cancel_do_not_wake_it`
- `tests/gateway/test_kanban_wake_contract.py::test_wake_cursor_reads_only_what_came_since_the_last_wake`

## 22. kanban-worker-wake-delta

Commits: `d856933c0b64ce8948ccae098219d0fa54db1c0e`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Tell a returning worker what changed after its last run.

Files:

- `agent/prompt_builder.py`
- `gateway/session_kanban.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_worker_wake.py`
- `tests/gateway/test_kanban_finite_admission.py`
- `tests/hermes_cli/test_kanban_worker_wake.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/gateway/test_kanban_finite_admission.py::test_first_run_card_turn_keeps_the_plain_wake`
- `tests/gateway/test_kanban_finite_admission.py::test_respawned_card_turn_carries_the_wake_delta_and_a_retry_keeps_it`
- `tests/hermes_cli/test_kanban_worker_wake.py::test_a_first_run_keeps_the_plain_wake`
- `tests/hermes_cli/test_kanban_worker_wake.py::test_a_respawned_worker_is_told_what_changed_since_its_last_run`
- `tests/hermes_cli/test_kanban_worker_wake.py::test_the_wake_cursor_reads_only_what_came_after_and_the_full_read_still_works`

## 24. compaction-landing

Commits: `1b5bcdff059c1708730d4bdd2b8d0bffbdf38a19`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Aim compaction at real prompt tokens and cap the summary.

The shared planner serves inline and prepared summaries.
It uses provider token readings, the landing target, and the summary cap.
An over-target protected floor returns `floor_exceeds_target` and logs the floor name once.
A prepared summary that exceeds the target after tail growth is discarded.

Files:

- `agent/agent_init.py`
- `agent/agent_init_compression_config.py`
- `agent/context_compressor.py`
- `agent/context_compressor_landing.py`
- `agent/context_compressor_summary.py`
- `agent/context_compressor_window.py`
- `agent/conversation_compression.py`
- `hermes_cli/config_defaults.py`
- `scripts/ci/docs_drift_baseline.json`
- `tests/agent/test_compaction_landing.py`
- `tests/agent/test_prepared_compaction.py`

Proof:

- `tests/agent/test_compaction_landing.py::test_a_capped_prompt_asks_for_no_action_log_and_a_size_limit`
- `tests/agent/test_compaction_landing.py::test_a_capped_summary_keeps_constraints_and_decisions_and_drops_the_logs`
- `tests/agent/test_compaction_landing.py::test_a_landing_target_makes_one_compaction_land_under_it`
- `tests/agent/test_compaction_landing.py::test_a_stale_anchor_leaves_the_ratio_alone`
- `tests/agent/test_compaction_landing.py::test_an_uncapped_prompt_keeps_the_upstream_template`
- `tests/agent/test_compaction_landing.py::test_an_uncapped_summary_is_unchanged`
- `tests/agent/test_compaction_landing.py::test_an_unreachable_target_keeps_the_default_tail`
- `tests/agent/test_compaction_landing.py::test_an_unreachable_target_reports_the_named_floor`
- `tests/agent/test_compaction_landing.py::test_config_keys_reach_the_settings`
- `tests/agent/test_compaction_landing.py::test_default_tail_lands_far_above_the_target`
- `tests/agent/test_compaction_landing.py::test_prepared_summaries_use_the_shared_landing_plan`
- `tests/agent/test_compaction_landing.py::test_the_landing_plan_never_widens_a_tail_set_below_its_floor`
- `tests/agent/test_compaction_landing.py::test_the_real_token_ratio_comes_from_the_provider_usage_anchor`
- `tests/agent/test_compaction_landing.py::test_the_target_defaults_to_a_share_of_the_trigger_and_never_exceeds_it`
- `tests/agent/test_compaction_landing.py::test_the_task_snapshot_drops_the_gateway_reply_note`
- `tests/agent/test_compaction_landing.py::test_the_task_snapshot_points_at_the_users_ask_not_a_wake`
- `tests/agent/test_compaction_landing.py::test_the_task_snapshot_reads_the_words_after_a_skill_scaffold`
- `tests/agent/test_compaction_landing.py::test_two_real_readings_separate_the_fixed_prompt_from_the_messages`
- `tests/agent/test_compaction_landing.py::test_verbatim_user_messages_hold_only_the_users_own_words`

## 25. kanban-advisory-checks

Commits: `f698df469458f37b808d4acc90341d5d7ef4b58c`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Treat mchen04 PR checks as advisory while checking PR identity.

Files:

- `hermes_cli/kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py`

Proof:

- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_completion_boundary_records_the_advisory_receipt`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_identity_and_head_still_gate`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_merged_claim_needs_github_to_say_merged`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_open_pr_without_required_checks_is_still_refused`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_passing_required_check_reads_as_before`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_required_check_on_another_head_stays_refused`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_required_checks_are_advisory_and_receipts_stay_honest`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_required_skipped_keeps_its_raw_conclusion`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_mchen04_wrong_head_evidence_wins_over_a_failure`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_other_owner_wrong_head_required_check_is_unchanged`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_other_owners_keep_required_checks_as_a_gate`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_check_run_on_another_head_stays_refused`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_claim_github_does_not_confirm_keeps_the_gate`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_head_moved_during_collection_is_stale`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_merged_pr_with_a_failed_check_is_accepted_and_keeps_the_failure`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_merged_pr_with_a_running_check_or_failed_status_is_accepted`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_open_pr_is_still_refused`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_passing_checks_read_as_before`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_receipt_keeps_raw_neutral_and_skipped_conclusions`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_mchen04_reread_refused_is_auth`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_other_owner_plan_path_receipts_are_unchanged`
- `tests/hermes_cli/test_kanban_plan_restricted_rules.py::test_other_owners_keep_the_gate_with_unchanged_reads`

## 27. kanban-contract-edit

Commits: `4d147388eb719692704104c5f117e5643426bfe4`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Change a card completion contract with recorded operator authority.

Files:

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_contract_edit.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_pr_resume.py`
- `tests/hermes_cli/test_kanban_contract_edit.py`
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_contract_edit.py::test_a_contract_change_voids_a_resume_recorded_against_the_old_contract`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_an_unchanged_contract_records_nothing`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_any_valid_contract_changes_with_its_old_value_recorded`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_authorization_must_be_an_operator_comment_on_the_card`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_contract_reason_and_authorization_are_required_and_validated`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_operator_cli_edit_lets_an_authorized_resume_proceed`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_worker_cli_edit_of_its_own_contract_is_refused`
- `tests/hermes_cli/test_kanban_contract_edit.py::test_workers_cannot_change_any_cards_contract`

## 29. kanban-stall-wake

Commits: `13c609c0f1e682c9a60ec74d83ea9ef60fea575a`, `19b6a446a9c452287c9c0047589248eb0c2714b9`.

Wake the following chat on a stalled card event.

Files:

- `gateway/kanban_wake_contract.py`
- `gateway/kanban_watchers_notifier.py`
- `locales/af.yaml`
- `locales/ar.yaml`
- `locales/de.yaml`
- `locales/en.yaml`
- `locales/es.yaml`
- `locales/fr.yaml`
- `locales/ga.yaml`
- `locales/hu.yaml`
- `locales/it.yaml`
- `locales/ja.yaml`
- `locales/ko.yaml`
- `locales/pt.yaml`
- `locales/ru.yaml`
- `locales/tr.yaml`
- `locales/uk.yaml`
- `locales/zh-hant.yaml`
- `locales/zh.yaml`
- `tests/gateway/test_kanban_stall_wake.py`

Proof:

- `tests/gateway/test_kanban_stall_wake.py::test_a_notify_subscription_gets_one_line_naming_the_stall`
- `tests/gateway/test_kanban_stall_wake.py::test_a_stall_is_a_wake_kind_and_never_a_muted_diagnostic`
- `tests/gateway/test_kanban_stall_wake.py::test_a_stalled_card_is_claimed_once`
- `tests/gateway/test_kanban_stall_wake.py::test_a_stalled_card_wakes_the_chat_that_follows_it_with_the_stall_and_its_next_step`

## 30. env-scrub-secret-suffixes

Commits: `1c4ba4831717b0e256799d93392d00713da6f4ba`.

Scrub any secret-shaped env name (`*_PASSWORD`, `*_SECRET`, `*_API_KEY`, `*_TOKEN`, `*_PRIVATE_KEY`, `*SECRET*`, `*PASSWORD*`) from terminal and non-inheriting children, with CLAUDE_CODE_OAUTH_TOKEN, the AWS chain and `HERMES_*` allowed and passthrough still able to admit a name.

Files:

- `tools/environments/local.py`
- `tools/environments/local_env_policy.py`
- `tests/tools/test_env_scrub_suffixes.py`
- `tests/tools/test_local_env_blocklist.py`

Proof:

- `tests/tools/test_env_scrub_suffixes.py::test_children_never_see_secret_shaped_names`
- `tests/tools/test_env_scrub_suffixes.py::test_terminal_child_observes_secret_shape_policy`
- `tests/tools/test_env_scrub_suffixes.py::test_passthrough_registration_still_admits_operator_secret`
- `tests/tools/test_env_scrub_suffixes.py::test_inheriting_child_keeps_secret_shaped_names`

## 31. pre-update-snapshot-complete

Commits: `84908fa0a2a8141bf386e61631e0612a3052eb2c`, `85ba872d8592e6ff9708a9b94d819b3fce51a777`, `cbe8bcb0320927fc1b0e49c629d11e5550849c70`, `4010162eae460bcc7995ee9822d32de2c6af791c`, `df94f5f8e77232fcfa177297a78b063a16f5f6a1`, `6dec1ebeeefc7caba98a372e48e865cd93772438`, `56404e4f7796559d8657d3c7ba9d3cb566bb4db9`, `affc0141ca3e42617761c6afacf6a00c943e2bea`, `2a0d09b81996808555bd364c4d912ef4d253d5c4`.

Pre-update snapshots copy every database whatever its size; the update no longer passes the 1 GiB per-file cap.
`hermes_cli/backup_snapshot_skip.py` skips a database only when the copy plus the `hermes_state_repair._disk_budget` headroom (256 MiB or 2% of the volume) does not fit on the snapshot's own disk, or that free space cannot be read.
The disk is read at the staging directory, so a `state-snapshots` symlink to another volume is budgeted against that volume.
The copy is charged as SQLite's committed image, `page_count` times `page_size` from a read-only connection, which is what `sqlite3.backup()` writes.
Many WAL updates to one page count as one page, and growth held only in the WAL is counted.
The main file plus its sidecars is charged only when SQLite cannot read the database.
Directory walks such as `kanban/boards` leave out SQLite `-wal`, `-shm` and `-journal` files, as full backups do.
A copied database already holds its committed WAL data, and a skipped database leaves none of its files behind.
The skip lands in `oversized_skipped`, so pruning stays off and the health check still reports the incomplete snapshot.
`max_file_size` stays for old updater callers.

Files:

- `hermes_cli/backup.py`
- `hermes_cli/backup_snapshot_skip.py`
- `hermes_state_repair.py`
- `hermes_cli/update_cmd_maint.py`
- `tests/hermes_cli/test_pre_update_snapshot_complete.py`
- `website/docs/getting-started/updating.md`
- `website/docs/user-guide/configuration.md`
- `website/docs/developer-guide/cli-internals.md`

Proof:

- `tests/hermes_cli/test_pre_update_snapshot_complete.py::test_update_snapshot_copies_profile_databases_larger_than_the_old_cap`
- `tests/hermes_cli/test_pre_update_snapshot_complete.py::test_snapshot_without_disk_headroom_records_skipped_databases_and_prunes_nothing`
- `tests/hermes_cli/test_pre_update_snapshot_complete.py::test_snapshot_budgets_space_on_the_destination_volume`
- `tests/hermes_cli/test_pre_update_snapshot_complete.py::test_snapshot_budgets_the_committed_database_image`
- `tests/hermes_cli/test_pre_update_snapshot_complete.py::test_snapshot_excludes_board_sidecars_and_preserves_committed_wal_rows`

## 32. kanban-restart-interrupt

Commits: `1a4ae618fecb11b04fa9ef850f85e46d3de043ad`, `23bfb4d9895c5b477792f30bc10a73db8a733b9a`.

A managed card worker lost while the gateway's restart wait is open ends its run `interrupted`, not `crashed`.
The owner's loss handler stamps `cause: gateway_restart` on the `worker_lost` event when `drain_control.restart_wait_open(authority.runner)` is true.
The crash sweep reads that cause, books the run `interrupted` with the error `interrupted by a gateway restart`, returns the card to ready at once, and leaves `consecutive_failures`, `last_failure_error` and the crash fingerprints alone.
The `on_kanban_worker_exited` payload carries `outcome: "interrupted"` and the `run_id`.
`respawn_guard_verdict` lets an interrupted latest run respawn immediately, past the success window and any stamped blocker text.
`build_worker_wake` opens the respawned worker's first message with the restart line, so it continues from the card sheet instead of treating the last turn as a failure.
Before this, every restart on this Mac booked each running card as a crash, charged its failure budget, and three cards lost in one tick tripped the sticky systemic block.

Files:

- `gateway/session_managed_worker.py`
- `gateway/session_kanban.py`
- `hermes_cli/kanban_owner_recovery.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `hermes_cli/kanban_worker_wake.py`
- `tests/hermes_cli/test_kanban_restart_interrupt.py`

Proof:

- `tests/hermes_cli/test_kanban_restart_interrupt.py::test_a_worker_lost_during_a_restart_wait_is_interrupted_and_respawns_at_once`
- `tests/hermes_cli/test_kanban_restart_interrupt.py::test_the_same_loss_outside_a_restart_wait_still_books_a_crash`
- `tests/hermes_cli/test_kanban_restart_interrupt.py::test_many_interrupted_cards_in_one_tick_never_trip_the_systemic_breaker`
- Shown able to fail: with the drain predicate forced false the first and third tests go red (`crashed`, three crashes in one tick).

## 33. provider-bench

Commits: `c0c927c536e184b6c9457c1f0e8eccf9f0392bc8`, `23bfb4d9895c5b477792f30bc10a73db8a733b9a`.

`agent/provider_bench.py` probes each of a profile's routes (the configured primary and every `fallback_providers` entry) with the profile's own credential before a card is dispatched, instead of a daily GET `/models`.
A probe is one chat completion with `max_tokens: 1` and a 10 s timeout, resolved through `resolve_runtime_provider` under the profile's secret scope (the same path the agent uses for `key_env` entries and the primary), so no secret is read or printed by the module.
401 or 403 benches the route for 30 minutes, 2xx clears it, any other status or a connection failure leaves the record alone.
Records live in `<HERMES_HOME>/state/provider-bench.json`, one per profile and route key, and a route is re-probed at most every 15 minutes, so the dispatch lock pays one file read per tick.
`respawn_guard_verdict` consults the verdict before the claim and holds the card as `respawn_guarded credential_rejected` when no route is usable, with one board comment naming each route and status; the card keeps its failure budget.
`build_kanban_policy` takes the first usable route as the admission model when the configured primary is benched and comments `Primary <provider> rejected the credential at <time>; this run uses <provider>.`
`_init_fallback_chain` drops benched entries from the agent's chain (cache read only).
`turn_exit_code` benches a route that rejected the credential mid-turn: a fallback route exits 75 (requeue, no failure), and 78 stays only for a rejected primary with no usable fallback.
Before this, a 401 from the fallback router mid-turn blocked a card with a sticky terminal-provider trip while the primary was healthy.

Files:

- `agent/provider_bench.py`
- `agent/agent_init_fallback.py`
- `gateway/session_kanban.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `hermes_cli/turn_exit.py`
- `tests/agent/test_provider_bench.py`

Proof:

- `tests/agent/test_provider_bench.py::test_a_rejected_fallback_is_benched_and_the_run_stays_on_the_primary`
- `tests/agent/test_provider_bench.py::test_a_rejected_primary_routes_the_run_to_the_fallback_with_one_comment`
- `tests/agent/test_provider_bench.py::test_both_rejected_holds_the_card_without_counting_a_failure`
- `tests/agent/test_provider_bench.py::test_an_unreachable_probe_leaves_the_record_alone`
- `tests/agent/test_provider_bench.py::test_a_credential_failure_on_a_fallback_benches_it_and_requeues`
- `tests/agent/test_provider_bench.py::test_a_rejected_primary_requeues_only_while_a_fallback_is_usable`

## 34. fallback-drops-codex-tier

Commits: `3ab2bbf054757939d03884f213dbd926c54007f8`.

`negotiate_request_tier` drops `service_tier: "ultrafast"` on any provider that is not first-party OpenAI (`_fast_mode_route_supported` by provider), so a profile's `ultrafast` preference never reaches a fallback router. The preference itself is unchanged; `priority` still passes through third-party routers as upstream intends.
Before this, a Codex 401 moved Forge workers to michael-router, which answered `HTTP 400: Invalid service_tier argument`, and two cards crashed (2026-11-07 17:28).

Files:

- `agent/codex_service_tiers.py`
- `tests/agent/test_codex_service_tiers.py`

Proof:

- `tests/agent/test_codex_service_tiers.py::test_a_fallback_to_a_custom_router_drops_ultrafast_without_changing_the_preference` (fails without the change)
- Live: `hermes -p forge chat --provider custom --base-url https://michael-router.vercel.app/v1 --model claude-opus-5-5` ran a shell tool and answered.

## 35. goal-mode-timed-wait

Commits: `2972f96ad90bc8108529fa222d569821cbf76ac9`, `0360c24022aeafe64c63bd1444726f9c93da3baf`.

`kanban_schedule` lets a goal_mode worker park when its reason starts `SCHEDULED_UNTIL=<ISO time>`. Other goal_mode schedules are still refused. Card wake resumes the card into the same goal loop, so `kanban_complete` still goes through the completion judge.
Before this, a goal_mode card waiting on an outside worker (Dot) could only block; each block went through triage and a root turn in #general, then a fresh worker. Cite: Dot t_5faca859, 16 refusals and ~400M Forge tokens in 60 h (2026-11-05..07).

Files:

- `tools/kanban_tools.py`
- `tests/tools/test_kanban_tools.py`

Proof:

- `tests/tools/test_kanban_tools.py::test_schedule_goal_mode_parks_on_a_timed_machine_wait` (fails without the change)
- `tests/tools/test_kanban_tools.py::test_schedule_goal_mode_refused` still holds for an untimed reason.

## 37. kanban-own-wait-pr-resume

Commits: `d915fa17ddac3d5dca55fde11263522553335149`, `c05af6a5f5419bb0b02a10f65ee0c0d8b0f226ee`.

A card that parked itself after its own worker linked an open PR resumes on wake, with no operator receipt.
`respawn_guard_verdict` lifts `active_pr` when this card's own worker wrote every holding PR comment, and the card's latest ended run has outcome `scheduled` and ended strictly after the newest of those comments.
A park is a deliberate pause, not a lost worker. The woken worker reads the PR on the card, so it is no duplicate-PR risk.
An operator who parks the card from the dashboard writes the same run outcome and gets the same result.
Still held: a run that ended in the same second as the PR comment or before it, a worker lost after the wake (crash, expired claim), a PR comment from anyone else, and a failed PR lookup (`pr_state_unknown`, which clears on the next good lookup).
Before this, ZergBoard card t_b16e2f4a stalled at both wakes on 2026-11-12, for 25 and 36 minutes, until an operator recorded `authorized_pr_resume`. That receipt binds to the last run id, so every new wait needed a new receipt.

Files:

- `hermes_cli/kanban_dispatch_pr_guard.py`
- `tests/hermes_cli/test_kanban_pr_state_guard.py` (already in hermes-local `CORE_TESTS`)
- `website/docs/user-guide/features/kanban.md`

Proof:

- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_own_timed_wait_after_own_worker_open_pr_respawns_on_every_wake` (fails without the change)
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_own_wait_lifts_only_when_the_run_ended_after_the_pr_comment`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_a_worker_lost_after_the_wake_still_holds`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_own_wait_after_someone_elses_pr_comment_still_holds`
- `tests/hermes_cli/test_kanban_pr_state_guard.py::test_own_wait_with_an_unanswered_pr_lookup_still_holds`
- Shown able to fail: with each condition of the exception removed in turn, one of the last four tests goes red.
- Replay on a scratch copy of the live board, with the PR read as open: the base holds t_b16e2f4a as `active_pr` at both 2026-11-12 wakes, and the change returns no hold.

## Dropped 2026-11-03

Topic ids stay stable; these numbers are retired.

- 5 search-rg-pgid: upstream 4f92c11 records rg's process group itself.
- 23 process-command-hash: unused; the compatibility commit's process-registry move went with it, so `tools/process_registry*.py` match upstream.
- 26 kanban-worker-tool-identity: unused and conflict-prone; `agent/delegation_context.py`, `tools/code_execution_env.py`, `tools/code_kernel.py`, `tools/environments/*` match upstream.
- 28 card-channel-read: no consumer.

Their proof tests (`tests/tools/test_search_rg_pgid.py`, `tests/tools/test_process_command_echo.py`, `tests/tools/test_kanban_worker_tool_identity.py`) are gone and left hermes-local's `CORE_TESTS`.

## Dropped 2026-11-16

- 36 anthropic-fable-5-2-catalog: upstream `9d97823a9b` lists `claude-fable-5-2` first in the curated Anthropic list. Upstream also keeps the `claude-fable-5.1` row, which the patch removed.
- PM membership compatibility: `PythonDeclaration.is_member` had one reader, hermes-local `tests/test_mnemosyne_plugin.py:24`. Upstream `installs_packages` holds the same value.
- Same-model review warning: upstream warns once per agent. The patch changed only the wording and the once-per-process gate.
- spawn-refusal-linux-only: a skip marker on one upstream test. The test fails on macOS on pure upstream too.

Their tests (`tests/pm/test_plugin_declaration_membership.py`, `tests/agent/test_background_review_reasoning_warning.py`) are gone.

## Kept with a caveat 2026-11-16

Topic 32 kanban-restart-interrupt stays, but its trigger no longer fires.
Upstream `579565e0e1` (restart when idle) waits for card workers on a normal restart, so nothing is lost there.
The patch stamps `cause: gateway_restart` only while `drain_control.restart_wait_open` is true, and upstream no longer calls `open_restart_wait` outside tests.
A forced restart (`hermes gateway restart --yes`, `hermes update --force`) still stops live workers. Read from the code, not run: the worker exits 130, and `_exit_code_kind` books any nonzero code as a crash.
Covering forced restarts needs a new trigger on the owner's stop cause. Until then, forced restarts charge each running card one failure.

## Requested additions

These additions supplement the numbered topics.

### tools-always-full-schema

Commits: `71f4d21385a133e70d633938257f78c555a43423`.

Set `tools.always_full_schema: [kanban_schedule]` in the profile config. Start a new session.
The pin overrides deferral. It does not enable unavailable tools.

Files:

- `tools/tool_search.py`
- `tools/kanban_tools_schemas.py`
- `hermes_cli/config_defaults.py`
- `website/docs/user-guide/configuration.md`
- `tests/tools/test_always_full_schema.py`

Proof:

- `tests/tools/test_always_full_schema.py::test_named_schema_pin_overrides_deferral_and_stays_profile_scoped`

### Delivery receipts

Commits: `3ecb9edc8127269b0fa3849496ca60c59bd677f8`, `3d5acfa8e676431c0413f3641f627aac5c444625`, `12826f2b6b28bd4235517c6600067ceb287fc946`, `93820955aac50f7991891f2b3d3a6a9adfc61a94`.

A send timeout records an unknown delivery. It does not log delivery or create a `-` message ID.
The adapter receipt settles direct and queued sends. No second send occurs.
Late callbacks retain the owning profile scope. Script-only jobs also receive the final outcome.

Files:

- `cron/live_delivery_receipts.py`
- `cron/scheduler_delivery.py`
- `cron/scheduler_bookkeeping.py`
- `cron/executions.py`
- `cron/delivery_queue.py`
- `cron/delivery_outcome.py`
- `tests/cron/test_live_delivery_timeout_receipt.py`
- `tests/cron/test_cron_failure_deliver.py`
- `tests/cron/test_queued_delivery_fencing.py`

Proof:

- `tests/cron/test_live_delivery_timeout_receipt.py::test_timeout_stays_unknown_until_the_adapter_receipt`
- `tests/cron/test_live_delivery_timeout_receipt.py::test_late_receipt_keeps_the_owning_profile_scope`
- `tests/cron/test_live_delivery_timeout_receipt.py::test_a_cancelled_confirmation_is_still_unknown`
- `tests/cron/test_cron_failure_deliver.py::TestFailureDeliverRouting::test_failure_without_failure_deliver_goes_to_deliver_targets`
- `tests/cron/test_cron_failure_deliver.py::TestFailureDeliverRouting::test_failure_deliver_local_is_silent_but_state_is_recorded`
- `tests/cron/test_cron_failure_deliver.py::TestFailureDeliverRouting::test_failure_deliver_explicit_target_wins_over_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestFailureDeliverRouting::test_success_ignores_failure_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestEscapedExceptionPath::test_escaped_failure_honors_failure_deliver_target`
- `tests/cron/test_cron_failure_deliver.py::TestEscapedExceptionPath::test_escaped_failure_with_failure_deliver_local_is_silent`
- `tests/cron/test_cron_failure_deliver.py::TestResolutionGrammar::test_for_failure_resolves_failure_deliver_value`
- `tests/cron/test_cron_failure_deliver.py::TestResolutionGrammar::test_for_failure_falls_back_to_deliver_when_unset`
- `tests/cron/test_cron_failure_deliver.py::TestResolutionGrammar::test_success_resolution_never_reads_failure_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestResolutionGrammar::test_local_yields_zero_failure_targets`
- `tests/cron/test_cron_failure_deliver.py::TestResolutionGrammar::test_comma_list_and_thread_grammar`
- `tests/cron/test_cron_failure_deliver.py::TestResolutionGrammar::test_legacy_list_value_is_flattened_like_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestToolSurface::test_create_stores_failure_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestToolSurface::test_create_without_failure_deliver_does_not_persist_the_key`
- `tests/cron/test_cron_failure_deliver.py::TestToolSurface::test_create_flattens_list_value_like_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestToolSurface::test_create_rejects_bad_bot_chat_profile_same_as_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestToolSurface::test_update_sets_and_clears_failure_deliver`
- `tests/cron/test_cron_failure_deliver.py::TestOutcomeBookkeeping::test_fd_local_failure_records_suppressed_not_delivered`
- `tests/cron/test_cron_failure_deliver.py::TestOutcomeBookkeeping::test_fd_explicit_target_failure_records_delivered`
- `tests/cron/test_cron_failure_deliver.py::TestOutcomeBookkeeping::test_success_outcome_still_reads_deliver_lane`
- `tests/cron/test_cron_failure_deliver.py::TestPreflightAndDashboardLanes::test_preflight_blocks_unknown_failure_platform`
- `tests/cron/test_cron_failure_deliver.py::TestPreflightAndDashboardLanes::test_preflight_failure_deliver_local_adds_no_platforms`
- `tests/cron/test_cron_failure_deliver.py::TestPreflightAndDashboardLanes::test_preflight_duplicate_lane_not_checked_twice`
- `tests/cron/test_cron_failure_deliver.py::TestPreflightAndDashboardLanes::test_dashboard_update_normalizes_failure_deliver`
- `tests/cron/test_queued_delivery_fencing.py::test_claim_stolen_after_the_ownership_sample_queues_no_ghost_send`
- `tests/cron/test_queued_delivery_fencing.py::test_dead_drain_owner_settles_the_run_it_fenced_unknown`
- `tests/cron/test_queued_delivery_fencing.py::test_origin_with_no_resolvable_target_is_not_configured_never_delivered`


## Added 2026-10-30

Commits: `325ab314d5c35e726a00d11aa773b898443bf61a`, `0fcf4f00cd1d357dbddcf1d00420aaf2036879e0`.

- **pool-bench-unscoped**: `model_cooldown_until(entry, None)` ignores entitlement benches, so one refused model cannot block every unscoped pool pick. Test: `tests/agent/test_anthropic_model_cooldowns.py`.
- **web-extract-fallback**: `web.extract_fallback_backend` sends failed URLs to a second backend. Test: `tests/tools/test_web_extract_fallback.py`.

## Added 2026-10-31

Commits: `9c8c010006e382ce67aab349a768fd33fec299f4`, `15e9c2c1fcad97a9ec077683ffcbf017648c226d`.

- **kanban-lifecycle-edit**: `hermes kanban edit <id> --goal-mode on|off --clear-stale-block --authorization-comment <id> --reason <why>` changes goal mode and clears a block record the card no longer waits on, with operator authorization (workers refused) and one audited `edited` event; `show --json` adds `goal_mode` and `block_recurrences`. Files: `hermes_cli/kanban_lifecycle_edit.py`, `hermes_cli/kanban.py`, `hermes_cli/kanban_parser.py`, `hermes_cli/kanban_output.py`, `website/docs/user-guide/features/kanban.md`. Test: `tests/hermes_cli/test_kanban_lifecycle_edit.py`.

## Replayed 2026-11-16 on d836448951

All kept source commits cherry-picked in live first-parent order. The two live merges were clean and held no extra changes.
Conflicts and resolutions:

- `website/docs/developer-guide/plugins/index.md` (topic 1): kept upstream's `post_llm_call` row with `side_question_token` and `review_signals`, and added the `background_review_prompt` row.
- `hermes_cli/kanban_db_dispatch.py` (topic 4): kept the worker-scoped env pop. Did not restore `strip_launch_profile_env`; upstream's `worker_client_env` allowlist owns that job.
- `agent/context_compressor.py` (topic 11): dropped the old copy of `_today_for_prompt`, which upstream moved to `context_compressor_prompt_rules.py`. The tool facts are collected before upstream's typed-turn prompt is built.
- `agent/context_compressor.py` (topic 17): upstream moved `_protect_head_size` to `context_compressor_window.py`; the bound-skill head floor moved with it.
- `website/docs/user-guide/messaging/index.md` (topic 18): kept upstream's `allow_silence` wording and added the `service_tier` lines.
- `agent/context_compressor.py` (topic 24): `LandingMixin` joins upstream's mixin list beside `SummaryInputSamplingMixin`. The update instruction feeds upstream's system message.
- `cron/scheduler_delivery.py` (delivery receipts): kept upstream's named DM topic routing; the patch had only removed a comment there.
- `agent/agent_init.py`, `agent/agent_init_compression_config.py` (compatibility commit): the config parser moved to the sibling again, rebuilt from upstream's current body, which reads the checkpoint guard from `conversation_compression_checkpoint`.
- `agent/context_compressor.py`, `agent/context_compressor_summary.py` (compatibility commit): `_build_summary_prompt` moved to the sibling again, rebuilt from upstream's current `[system, user]` body.
- `hermes_cli/config_defaults.py` (compatibility commit): kept upstream's `VOICE_DEFAULTS` import beside `KANBAN_DEFAULTS`. Upstream's kanban defaults hold no key that `config_defaults_kanban.py` lacks.
- `tools/environments/local.py`, `tools/environments/local_env_policy.py` (topic 30): kept upstream's config-declared credential rules and added the secret-shaped name rule.
- `hermes_cli/backup.py` (topic 31, twice): kept upstream's `sqlite_safe_read` and `backup_atomic` imports beside `snapshot_skips`.
- `gateway/session_kanban.py` (topic 33): the bench exit code and upstream's redaction of the worker's last output both stay.

Adaptations after the replay:

- The summary request is a system and a user message upstream. The fact rules and the reply-label rules join the system message. The label check also reads the typed turns.
- `tests/agent/test_compression_summary_facts.py`, `tests/agent/test_compaction_landing.py`: read the prompt from both messages.
- `tests/cron/test_live_delivery_timeout_receipt.py`: patches the standalone send at `gateway.outbound.send`, where upstream moved it.

Verification (runtime proof on the `mini` host, the path `check_runtime.py` uses, on tree `e286559f16aaade3a8608a60746ed3ef62add52e`):

Candidate, standard proof (the 40 `CORE_TESTS` files and the hermes-local suites at `410fc66`):

- `CORE_TESTS`: 40 files, 639 passed, 0 failed, 1 skipped. Source links: 8 passed.
- hermes-local `tests`: 484 run, 2 errors. `operations/tests`: 1153 run, 11 failed. `~/.hermes/scripts/tests`: 44 run, 1 failed. mikebot-texting: 1 passed.

Baseline, pure upstream `d836448951` (the 14 `CORE_TESTS` files upstream has, hermes-local at `b695ddd`):

- `CORE_TESTS`: 14 files, 173 passed, 0 failed, 1 skipped.
- hermes-local `tests`: 484 run, 3 failed. `operations/tests`: 1097 run, 30 failed. `~/.hermes/scripts/tests`: 44 run, 1 failed.

Every hermes-local failure on the candidate also fails on the baseline. None comes from a kept patch:

- `test_mnemosyne_plugin` reads `is_member`, a dropped patch. Line 24 needs `installs_packages`.
- `test_imessage` reads the locale key `gateway.notify.turn_error_detailed`, which upstream `257d8532c9` removed.
- `test_script_imports`: `operations/news_markets_v4_run.py` imports `tools.send_message_targets`. Upstream `f8acc26834` moved it to `gateway.outbound.targets`.
- `test_log_posters` (11): `~/.hermes/scripts/usage_snapshot.py` became a wrapper during this session, and its runner is not in the proof snapshot. The same suite passed (1097 of 1097) on the candidate one hour earlier.

The patches fix 19 `test_kanban_stuck_alert` failures that pure upstream shows.

Wide run (every test file that names a patched module, 8 workers):

- Candidate: 758 files, 11,582 passed, 22 failed, 442 errors. Baseline: 728 files, 10,791 passed, 16 failed, 92 errors.
- 16 failures and 92 errors are common to both. 350 candidate errors are `Too many open files` in `tests/tui_gateway/test_tui_gateway_server.py`; run alone on one Mac, that file fails the same 6 tests on both trees.
- Candidate only, by design: 4 tests in `tests/tools/test_profile_key_env_scrub.py`. Upstream expects `MY_CUSTOM_API_KEY` and an unpointed `CORP_LLM_TOKEN` to reach children; topic 30 scrubs both.
- Candidate only, open: `tests/agent/test_compression_stall_deterministic_fallback.py::test_over_window_request_commits_the_deterministic_fallback_on_the_first_stall`. It passes on the live predecessor and on pure upstream. Upstream's new tail budget and the topic 24 landing plan together start the tail on an assistant row, so the fallback summary merges into that row. The test expects a row of its own.
- `tests/hermes_cli/test_kanban_db.py::test_infrastructure_spawn_refusal_never_charges_the_card` fails on macOS without the dropped skip marker.
