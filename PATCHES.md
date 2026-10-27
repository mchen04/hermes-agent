# Hermes local patch manifest

Upstream base: `085f562af53a6fedadac3681e2c498fc71316132`.
Live predecessor: `7fd9283476d363cb63b4b22f4cfb754614594dcc`.
Delivery commit: `8e3a45d7e7edd29d76a24664efb0cddfc25e428e`.
Verified content tree: `70384efbb7e857328fd718339775ae24c2ce9938`.
Kept topics: **29**, from **31** source commits in first-parent order.
Use this manifest instead of searching historical `LOCAL-PATCH` subjects.
All 199 audited added test entries remain present. All 24 required runtime-check paths exist.
All 31 replayed commits preserve the original author metadata and source-commit footer.
The final merge keeps the verified content tree. Upstream and the live predecessor are both ancestors.
The identity cleanup adds and removes `WORKLOG.md`. The final tree has no worklog.

The shared compatibility commit is `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.
It moves code into topic modules and uses the upstream config reader.

## 1. background-review-prompt-hook

Commits: `9e57defe17a828ba8a21e9d62027f922db49f869`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `17769eaf0d11b8c07cca70e108c81153b9fc332a`.

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

Commits: `3fe30b860024946cb5804efcf570f39ce65626f0`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

Accept a merged PR when no checks are required.

Files:

- `hermes_cli/kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py`

Proof:

- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_merged_pr_with_required_checks_still_needs_them`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_merged_pr_without_required_checks_is_accepted`
- `tests/hermes_cli/test_kanban_merged_no_required_checks.py::test_open_pr_without_required_checks_is_refused`

## 4. kanban-dispatch-task

Commits: `108065cae3d522de96adbe66b34d1f494ce6684f`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

## 5. search-rg-pgid

Commits: `67c2296b3eaf4dd00340f7e9c9a5357cafb1d1c8`.

Record rg process identity before it can exit.

Files:

- `tests/tools/test_search_rg_pgid.py`
- `tools/environments/local.py`
- `tools/file_operations_search.py`

Proof:

- `tests/tools/test_search_rg_pgid.py::test_kill_helper_tolerates_an_exited_unreaped_child_without_a_recorded_pgid`
- `tests/tools/test_search_rg_pgid.py::test_native_rg_runner_survives_rg_exiting_after_poll`
- `tests/tools/test_search_rg_pgid.py::test_spawn_records_the_group_and_its_start_fingerprint`

## 6. kanban-authorized-pr-resume

Commits: `707fb5850aa3e06680a10d7b37428075540d978d`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `f2b6d3ec4c0595ab39eea636e845bce0f512e23d`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

Record a repeated dispatcher hold once.

Files:

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_pr_guard.py`
- `tests/hermes_cli/test_kanban_dispatch_task.py`

Proof:

- `tests/hermes_cli/test_kanban_dispatch_task.py::test_dispatch_task_respects_dependencies`
- `tests/hermes_cli/test_kanban_dispatch_task.py::test_held_card_records_its_guard_once_per_hold_not_per_tick`

## 8. kanban-pr-state-guard

Commits: `00dbfe7b3b8dbf137dfdc42fc6362ac8a5d5d925`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `faafa0dae8ddf6945948d94dabfac0326ac97101`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `229b6498a2dae7b0d6b64560aa4dcb3345f54ee7`.

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

Commits: `fd133b82d39b5f37a0b030d95b2d821d3771003a`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `18ff0dc5646584a785ade3992c7c187977bad4b9`.

Give worker context only to that card's own worker.

Files:

- `tests/tools/test_kanban_incremental_read.py`
- `tools/kanban_task_updates.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

Proof:

- `tests/tools/test_kanban_incremental_read.py::test_reader_that_is_not_the_task_worker_gets_fields_without_worker_context`

## 13. kanban-plan-restricted-rules

Commits: `c6c8ae3579e04b517182d6c5dc4bd96cadb45494`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `e917e9a55d215d8441b4de93500d453778680f62`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `a0263ac680e63190f5012816093adf85fd1d683d`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `d3c8c5683bb464150c2882f7441c64ca53d84541`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `7146823b1fd9470a39a29d96e846a9a538348f27`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`, `6119bc884f7c3359b0fa5ccebfb92b529eb8ecc4`.

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

Commits: `b0467130ce54fc5edef5a96beba7c4a8b0daaaab`, `8f014d37bcf295e38ab95130e098e1eb21fcfb22`.

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

Commits: `ffe1b3be257916668c1bdb132d9ca9f78118c143`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `2ee3b3b6fa5f6d4d7f61e6bd9d7022a3221e696f`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `2abf0805fe1dff373a55f84714beb56f9d581169`, `8324e6c7507dcd2e5eec5a21d59bd46bd7f9b1ae`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `3b8d758113e53bfce308c93200b5c463b13b6a9b`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

## 23. process-command-hash

Commits: `d7297c127acefef835154554ae2b6c12ebd627d4`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

Shorten echoed process commands and keep a hash.

Files:

- `hermes_cli/config_defaults.py`
- `tests/tools/test_process_command_echo.py`
- `tools/process_command_echo.py`
- `tools/process_registry.py`
- `tools/process_registry_results.py`
- `website/docs/user-guide/configuration.md`

Proof:

- `tests/tools/test_process_command_echo.py::test_short_commands_and_the_list_view`
- `tests/tools/test_process_command_echo.py::test_status_and_wait_results_return_a_hash_instead_of_the_instruction`

## 24. compaction-landing

Commits: `2a63a9ce7eb3aec014cc212f176cddd62716dd04`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

Commits: `4e8eb12075b90ef74467c6f79b007b4240b624d3`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

## 26. kanban-worker-tool-identity

Commits: `dcb43c2f7c2b722fbdd8a39d778d7a9323eaf94c`, `e4600fa820f86c61954e9b38397baf83f688c12f`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

Label a verified worker's own tool children with its card and run.

Files:

- `agent/delegation_context.py`
- `gateway/session_kanban.py`
- `hermes_cli/kanban_resume_guard.py`
- `tests/gateway/fixtures/kanban_owner_probe.py`
- `tests/gateway/test_session_kanban.py`
- `tests/tools/_kanban_identity_harness.py`
- `tests/tools/test_kanban_worker_tool_identity.py`
- `tools/code_execution_env.py`
- `tools/code_kernel.py`
- `tools/environments/base_session_env.py`
- `tools/environments/local.py`
- `tools/process_registry.py`
- `website/docs/user-guide/features/kanban-worker-lanes.md`

Proof:

- `tests/tools/test_kanban_worker_tool_identity.py::test_concurrent_bound_workers_children_carry_only_their_own_card`
- `tests/tools/test_kanban_worker_tool_identity.py::test_no_label_without_a_live_binding_of_this_interpreter`

## 27. kanban-contract-edit

Commits: `58f6191f45f51da4f80e990b92d63e9eb65d8b12`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

## 28. card-channel-read

Commits: `ed1932161ace2fd2b0f175362852d2e8898979d9`.

Give card turns the profile's platform-only chat tools.

Files:

- `gateway/session_kanban.py`
- `tests/gateway/test_session_kanban.py`

Proof:

- `tests/gateway/test_session_kanban.py::test_claim_freezes_task_policy_and_rejects_forgery`
- `tests/gateway/test_session_kanban.py::test_claim_reclaimed_before_admission_marker_is_refused`
- `tests/gateway/test_session_kanban.py::test_late_launcher_cannot_stamp_replacement_claim`
- `tests/gateway/test_session_kanban.py::test_real_dispatcher_lifecycle`

## 29. kanban-stall-wake

Commits: `0d6a12e94c907081239007117c3d9d0bfdf53684`, `c569d345e5a7ef5170cdf727b9aeb22a570b97f7`.

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

## Requested additions

These additions supplement the 29 kept topics.

### tools-always-full-schema

Commits: `0cf53e8ccacc3f31e3bc9c011d4635c07a4dd537`.

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

Commits: `dd4a1dd912f779c6865f604ff44cd4cb3989b3fa`, `a0409d63e8e94d96b1129270cdcddab3414a763d`, `e902dfc9e3c5cf20b5e1b9e76bceb63f3d90af5b`, `a9370cf020cbe92d6840d9bc651c57944eade857`.

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

### PM membership compatibility

Commits: `c9fd54c412d06475ed8af9e639a6a990c5fd91a0`.

The local plugin check can read `PythonDeclaration.is_member`.
The field follows upstream package admission. Empty projects remain outside the environment.

Files:

- `pm/plugin_declarations.py`
- `tests/pm/test_plugin_declaration_membership.py`

Proof:

- `tests/pm/test_plugin_declaration_membership.py::test_membership_field_uses_the_current_package_admission_rule`
- `tests/pm/test_plugin_declaration_membership.py::test_an_empty_pyproject_does_not_become_a_member`

### Same-model review warning

Commits: `3873aaa05fc9a6b8bee6313b0d890675d61af08d`.

The warning fires once per process. Review behavior stays the same.
The message names `auxiliary.background_review.reasoning_effort` and explains prompt-cache preservation.

Files:

- `agent/background_review.py`
- `locales/en.yaml`
- `tests/agent/test_background_review_reasoning_warning.py`

Proof:

- `tests/agent/test_background_review_reasoning_warning.py::test_same_model_reasoning_warning_fires_once_across_agent_instances`

## Verification

Each source commit passes its touched test files before commit.
Behavior tests fail before their corresponding fixes. The test-only cleanup has no behavior change.
New receipt checks cover success, failure, both receipt timings, both delivery paths, and script-only jobs.

`check-final.log`:

- scripts/check: 13 checks, ok

`full-final.log`:

- === Summary: 5568 files, 54654 tests passed, 46 failed, 11 errors, 1396 skipped; FAILED: 28 of 5568 files (3 at import/collection) (100% complete) in 1375.8s (15 workers) ===

`hl-final-tree.log`:

- == tests rc=1
- Ran 223 tests in 198.117s
- FAILED (failures=1, skipped=2)
- FAIL: test_the_gate_runs_at_low_priority_with_two_jobs (test_learning.LearningTests.test_the_gate_runs_at_low_priority_with_two_jobs)
- == operations/tests rc=1
- Ran 340 tests in 46.329s
- FAILED (failures=1, skipped=1)
- FAIL: test_held_card_shows_the_hold (test_board_now.ScriptTest.test_held_card_shows_the_hold)
- == skills/messaging/mikebot-texting/tests rc=0
- Ran 1 test in 0.003s
- OK

`upstream-same-checkout.log`:

- === Summary: 28 files, 480 tests passed, 45 failed, 11 errors, 20 skipped; FAILED: 27 of 28 files (3 at import/collection) (100% complete) in 75.6s (15 workers) ===

Commands:

```sh
python3 scripts/check
bash scripts/run_tests.sh tests/agent tests/gateway tests/hermes_cli tests/tools tests/cron
PYTHONDONTWRITEBYTECODE=1 bash /private/tmp/claude-501/-Users-michaelchen/d44dd38e-0ff4-4c7c-a4f0-31c3b3d591bf/scratchpad/work/hl_test.sh /private/tmp/claude-501/-Users-michaelchen/d44dd38e-0ff4-4c7c-a4f0-31c3b3d591bf/scratchpad/work/hl-ref /Users/michaelchen/Worktrees/hermes-core-update
```

The upstream comparison uses the same checkout, host, and test activation.
It temporarily checks out `origin/main`, runs the failing files, and restores this branch.
The complete outputs remain in `.rebuild/upstream-same-checkout.log` and `.rebuild/full-final.log`.
The intermittent browser failure also reproduces on upstream in seven of eight concurrent file runs.
Those runs use the same checkout and normal test activation. Their logs match the three-second reconciliation error.
See `.rebuild/upstream-browse-same-checkout-01.log` through `-08.log` for those results.
The external priority test inherits nice 5. macOS caps its relative adjustment at 20.
The test expects absolute priority 19. The OS denies resetting this process to priority 0.
This environmental exception awaits the user decision. No test is skipped or weakened.
The known board-hold failure depends on the timezone. The live runtime is not changed or tested.

### Upstream failure evidence

Every final failure and error below also fails on `origin/main`.
No touched test file fails. No new failing test or error remains.

Failed:

- `tests/agent/lsp/test_workspace.py::test_resolve_workspace_for_file_survives_deleted_cwd`
- `tests/agent/test_compaction_prompt_rebuild.py::TestWorkspaceSnapshotPinnedAcrossCompaction::test_agent_that_did_not_build_the_prompt_replays_the_persisted_snapshot`
- `tests/agent/test_compaction_prompt_rebuild.py::TestWorkspaceSnapshotPinnedAcrossCompaction::test_binding_the_launch_dir_explicitly_replays_the_pin`
- `tests/agent/test_compaction_prompt_rebuild.py::TestWorkspaceSnapshotPinnedAcrossCompaction::test_persisted_prompt_without_a_snapshot_does_not_pin_an_empty_one`
- `tests/gateway/test_session_client_env.py::test_auto_started_gateway_does_not_inherit_client_behaviour_env`
- `tests/hermes_cli/test_browse_schema_drift.py::test_concurrent_browses_without_a_gateway_all_succeed`
- `tests/hermes_cli/test_desktop_swap_file_lock_retry.py::test_swap_retries_transient_permission_error_then_promotes`
- `tests/hermes_cli/test_desktop_update_verify.py::test_caller_root_without_a_packaged_app_falls_back_to_the_checkout`
- `tests/hermes_cli/test_desktop_update_verify.py::test_readable_packaged_entry_passes`
- `tests/hermes_cli/test_enable_no_capabilities.py::test_enable_consent[None-False-True-n-False-0]`
- `tests/hermes_cli/test_enable_no_capabilities.py::test_enable_consent[None-None-None-n-None-0]`
- `tests/hermes_cli/test_enable_no_capabilities.py::test_enable_consent[None-None-True-n-True-0]`
- `tests/hermes_cli/test_enable_no_capabilities.py::test_enable_consent[None-True-None-n-True-0]`
- `tests/hermes_cli/test_enable_no_capabilities.py::test_enable_consent[caps1-None-None-n-None-0]`
- `tests/hermes_cli/test_enable_no_capabilities.py::test_enable_consent[caps5-None-None-y-True-1]`
- `tests/hermes_cli/test_gateway_runtime_ensure.py::test_unmanaged_runtime_drops_session_config_bypass_flags`
- `tests/hermes_cli/test_kanban_db.py::test_infrastructure_spawn_refusal_never_charges_the_card`
- `tests/hermes_cli/test_kanban_worktree_teardown.py::test_cleanup_proceeds_when_cwd_was_deleted`
- `tests/hermes_cli/test_plugin_validate.py::test_portable_validation_fails_orphan_and_reports_availability`
- `tests/hermes_cli/test_plugin_worker_lifecycle.py::test_active_reinstall_does_not_overwrite_a_later_disable[enable]`
- `tests/hermes_cli/test_plugin_worker_lifecycle.py::test_active_reinstall_does_not_overwrite_a_later_disable[no_enable]`
- `tests/hermes_cli/test_plugins_cmd_enable_disable_nested.py::test_dependency_free_enable_no_churn_and_tool_override_fails_closed`
- `tests/hermes_cli/test_plugins_cmd_enable_disable_nested.py::test_fallback_compares_the_preinteraction_selection[False]`
- `tests/hermes_cli/test_plugins_cmd_enable_disable_nested.py::test_nested_enable_disable_and_composite_use_canonical_key[observability/trace-leaf]`
- `tests/hermes_cli/test_plugins_cmd_enable_disable_nested.py::test_nested_enable_disable_and_composite_use_canonical_key[trace-leaf]`
- `tests/hermes_cli/test_plugins_cmd_enable_disable_nested.py::test_nested_enable_disable_and_composite_use_canonical_key[trace-manifest]`
- `tests/hermes_cli/test_plugins_picker_effective_state.py::test_only_flipped_rows_are_written`
- `tests/hermes_cli/test_session_recovery_lost_and_found.py::test_unreadable_schema_without_cli_names_the_sqlite3_requirement`
- `tests/hermes_cli/test_shared_metrics_harness.py::test_foreground_terminal_commands_count_by_kind_and_outcome`
- `tests/hermes_cli/test_tui_npm_install.py::test_runtime_node_comes_from_pm_without_legacy_repair`
- `tests/hermes_cli/test_uninstall_full_wipe_sweep.py::test_remove_dashboard_launchd_jobs_boots_out_and_deletes_matching_plists`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_clean_update_defers_desktop_owned_serve_and_clears_marker`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_clean_update_escalates_surviving_serve_as_unaccounted`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_interrupt_between_pull_and_restart_leaves_marker`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_marker_written_after_pull_cleared_after_successful_restart`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_pending_fleet_restart_cleared_instead_of_exit_1`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_up_to_date_update_leaves_current_fleet_alone`
- `tests/hermes_cli/test_update_fleet_restart_pending.py::test_up_to_date_update_still_restarts_a_stale_gateway`
- `tests/hermes_cli/test_update_target_identity.py::test_stable_git_uses_remote_identity_without_moving_local_tags[fetch-refused]`
- `tests/tools/test_bot_desktop_resources.py::test_start_refuses_and_status_explains_when_memory_is_short`
- `tests/tools/test_delegate.py::TestDelegateTask::test_child_dedicated_db_follows_parents_db_path`
- `tests/tools/test_terminal_pipeline_status.py::test_capturing_pipestatus_changes_nothing_the_model_sees[echo "unbalanced-2-]`
- `tests/tools/test_terminal_pipeline_status.py::test_capturing_pipestatus_changes_nothing_the_model_sees[echo $(echo unclosed-2-]`
- `tests/tools/test_terminal_pipeline_status.py::test_capturing_pipestatus_changes_nothing_the_model_sees[echo 'unbalanced-2-]`
- `tests/tools/test_terminal_pipeline_status.py::test_capturing_pipestatus_changes_nothing_the_model_sees[echo hi \\-0-hi \\\n]`
- `tests/tools/test_transcription_tools.py::TestTranscribeLocalExtended::test_config_device_and_compute_type_passed_to_whisper`

Errors:

- `tests/agent/test_vault_key_storage.py`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_rejected_store_is_not_reported_as_runtime_available[artifacts]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_rejected_store_is_not_reported_as_runtime_available[malformed]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_rejected_store_is_not_reported_as_runtime_available[missing-binary]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_rejected_store_is_not_reported_as_runtime_available[sparse]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_rejected_store_is_not_reported_as_runtime_available[target]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_rejected_store_is_not_reported_as_runtime_available[version]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_sealed_store_extension_matches_launched_tool[absent]`
- `tests/hermes_cli/test_doctor_pm_store_probe.py::test_sealed_store_extension_matches_launched_tool[stale]`
- `tests/hermes_cli/test_vault_key_cli.py`
- `tests/tools/test_fal_sdk.py`
