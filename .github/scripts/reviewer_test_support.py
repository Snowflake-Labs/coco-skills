"""Fixed, bounded test evidence; proposed counts never confer verification."""
import json

MAX_COUNT = 10000
MAX_EVIDENCE_BYTES = 512
FIELDS = {'expected', 'executed', 'failed', 'skipped'}

# Deliberately independent of discovery: deleting/renaming required tests fails closed.
REQUIRED_TESTS = (
    'test_reviewer.IngestionTests.test_symlinks_root_intermediate_file_directory',
    'test_reviewer.IngestionTests.test_hardlink_rejected',
    'test_reviewer.IngestionTests.test_fifo_and_socket_rejected',
    'test_reviewer.IngestionTests.test_limits',
    'test_reviewer.IngestionTests.test_replacement_between_stat_and_open',
    'test_reviewer.LauncherTests.test_command_has_no_mounts_and_has_restrictions',
    'test_reviewer.LauncherTests.test_remote_daemon_denied',
    'test_reviewer.LauncherTests.test_timeout_cleans_container_and_never_falls_back',
    'test_reviewer.LauncherTests.test_untrusted_container_output_never_echoed',
    'test_reviewer.CollectionDeadlineTests.test_real_stalled_child_killed_and_reaped',
    'test_reviewer.CollectionDeadlineTests.test_worker_errors_and_output_are_private',
    'test_reviewer.NetworkIsolationTests.test_loopback_only_namespace_accepted',
    'test_reviewer.NetworkIsolationTests.test_network_enabled_namespace_rejected',
    'test_workstream6.SnapshotTests.test_private_archive_runtime_copy_ownership_contract',
    'test_reports.ReportTests.test_coverage_requires_unique_source_bound_unsupported_findings',
    'test_reports.ReportTests.test_legitimate_coverage_and_error_statuses_remain_valid',
    'test_reports.HostProvenanceTests.test_correctly_bound_partial_coverage_fails_closed',
    'test_model_contract.ModelContractTests.test_lf_coordinates_empty_trailing_and_non_lf_separators',
    'test_model_contract.ModelContractTests.test_diagnostics_and_markdown_use_lf_offsets',
    'test_model_contract.ModelContractTests.test_frontmatter_uses_lf_boundaries_and_supports_crlf',
    'test_reviewer.ContainerSmokeTests.test_valid_review_inside_container',
    'test_reviewer.ContainerSmokeTests.test_runtime_write_network_and_privilege_boundaries',
    'test_ci.GitSelectionTests.test_two_component_symlinks_selected_and_blocked',
    'test_ci.GitSelectionTests.test_rename_and_complete_deletion',
    'test_ci.DeletionTests.test_parent_symlinks_never_count_as_removed',
    'test_ci.CITests.test_workflow_builds_and_invokes_only_trusted_tooling',
    'test_ci.CITests.test_workflow_triggers_and_trusted_verification',
    'test_ci.TestEvidenceTests.test_bootstrap_runs_only_synthetic_tests_and_discards_raw_output',
    'test_ci.TestEvidenceTests.test_untrusted_output_never_reaches_host_summary',
    'test_ci.RepositorySnapshotTests.test_selected_inputs_preserve_repository_layout',
    'test_policy.PolicyTests.test_contributor_values_are_not_broadened',
    'test_policy.PolicyTests.test_status_values_are_only_advisory',
    'test_policy.PolicyTests.test_employee_claim_in_either_alias_cannot_be_hidden',
    'test_policy.PolicyTests.test_docs_table_matches_executable_matrix',
    'test_policy.LicenseTemplateTests.test_templates_equal_complete_pinned_public_sources',
    'test_policy.LicenseTemplateTests.test_phrase_only_missing_tampered_and_extra_terms_rejected',
    'test_policy.PackagingTests.test_docker_resource_layout_without_docker_or_build',
    'test_reports.ReportTests.test_fields_remain_distinct_and_actionable',
    'test_reports.ReportTests.test_canaries_paths_values_controls_and_links_not_in_safe_reports',
    'test_reports.ReportTests.test_stable_source_survives_ordinal_and_line_shifts',
    'test_reports.HostProvenanceTests.test_provenance_tampering_fails_closed',
    'test_reports.PrivateOutputTests.test_same_captured_snapshot_despite_candidate_changes',
    'test_reports.PrivateOutputTests.test_prohibited_destinations_rejected_before_collection',
    'test_reports.PrivateOutputTests.test_parent_replaced_by_symlink_between_check_and_create',
    'test_reports.PrivateOutputTests.test_file_created_between_preflight_and_write_is_not_overwritten',
    'test_reports.PrivateOutputTests.test_cli_fixed_destination_error_no_path_or_canary',
    'test_workstream5.ComparisonTests.test_incomplete_unsafe_missing_baseline_never_resolved',
    'test_workstream5.ComparisonTests.test_line_ordinal_shift_and_distinct_metadata_fields',
    'test_workstream5.GitComparisonTests.test_whole_bundle_scan_same_image_and_unchanged_head_error',
    'test_workstream5.GitComparisonTests.test_wrong_revisions_nofollow_checkouts_and_dirty_snapshots',
    'test_workstream5.GitComparisonTests.test_whole_skill_rename_explicit_mapping_no_heuristic',
    'test_workstream5.GitComparisonTests.test_file_rename_mapping_and_line_shift',
    'test_workstream5.GitComparisonTests.test_addition_deletion_and_missing_baseline',
    'test_workstream5.MarkdownTests.test_examples_fences_inline_and_prose_not_dependencies',
    'test_workstream5.MarkdownTests.test_balanced_nested_labels_destinations_titles_and_images',
    'test_workstream5.CatalogTests.test_expected_neighbors_and_deterministic_order',
    'test_workstream6.SnapshotTests.test_root_candidate_overlap_symlink_and_wrong_installation_rejected',
    'test_workstream6.SnapshotTests.test_save_requires_exact_digest_and_new_private_destination',
    'test_workstream6.ActionTests.test_fork_event_uses_only_exact_shas_not_candidate_assertions',
    'test_workstream6.ActionTests.test_missing_failed_skipped_coverage_prevents_scan',
    'test_workstream6.ActionTests.test_digest_mismatch_blocks_before_verification',
    'test_workstream6.ActionTests.test_raw_machine_output_extra_fields_and_canaries_rejected',
    'test_workstream6.ActionTests.test_machine_provenance_image_catalog_and_revision_binding',
    'test_workstream6.ActionTests.test_summary_no_links_or_raw_content_and_rejects_candidate_destination',
    'test_workstream6.ForkGitRehearsal.test_synthetic_fork_exact_git_selection_with_unavailable_runtime',
    'test_workstream7.AnalysisTests.test_one_review_decode_and_manifest_for_pair',
    'test_workstream7.AnalysisTests.test_arbitrary_packet_inputs_still_require_fresh_eligibility',
    'test_workstream7.AnalysisTests.test_baseline_corpus_report_and_packet_equivalence',
    'test_workstream7.CacheTests.test_context_invalidation_every_component',
    'test_workstream7.CacheTests.test_file_modes_owner_links_and_special_files',
    'test_workstream7.CacheTests.test_file_replacement_between_stat_and_open_and_during_read',
    'test_workstream7.CacheTests.test_expiration_future_and_collision_never_overwrite_or_prune',
    'test_workstream7.CacheTests.test_corrupt_duplicate_oversize_and_provenance_fail_safe',
    'test_workstream7.PreviewTests.test_authoritative_api_never_uses_cache_and_same_user_limit',
    'test_workstream7.PreviewTests.test_unsafe_recollection_blocks_hit_and_corrupt_falls_back',
    'test_workstream7.SelectionTests.test_proposed_scope_exact_revisions_and_all_tooling_inputs',
    'test_workstream8.BrokerTests.test_disabled_by_default_before_any_provider_or_worker',
    'test_workstream8.BrokerTests.test_trusted_interface_only_and_http_unavailable_even_with_consent',
    'test_workstream8.BrokerTests.test_consent_bound_to_wire_config_catalog_and_explicit_transmission',
    'test_workstream8.BrokerTests.test_fresh_worker_eligibility_no_forged_report_or_cache_authority',
    'test_workstream8.BrokerTests.test_schema_rubric_framing_counted_and_input_ceiling_exact',
    'test_workstream8.BrokerTests.test_conservative_reservation_uses_output_ceiling_and_fixed_fee',
    'test_workstream8.BrokerTests.test_invalid_response_no_prose_citation_override_or_custom_objects',
    'test_workstream8.BrokerTests.test_provider_and_tokenizer_stalls_both_supervised_and_reaped',
    'test_workstream8.BrokerTests.test_bounded_ipc_rejects_oversize_and_malformed_without_deserialization',
    'test_workstream8.BrokerTests.test_worker_stdout_stderr_environment_and_no_pickle',
    'test_workstream8.BrokerTests.test_one_call_no_retry_even_on_failure',
    'test_workstream8.EditorialBenchmarkTests.test_no_human_judgments_means_unmeasured_not_fabricated_quality',
    'test_workstream8.EditorialBenchmarkTests.test_missing_duplicate_unknown_and_nonhuman_ratings_refused',
    'test_advisory_cache.AdvisoryCacheTests.test_disabled_and_no_cache_default_never_touch_files',
    'test_advisory_cache.AdvisoryCacheTests.test_miss_defaults_to_no_call_and_explicit_one_call_populates',
    'test_advisory_cache.AdvisoryCacheTests.test_hit_fresh_eligibility_no_provider_and_report_unchanged',
    'test_advisory_cache.AdvisoryCacheTests.test_consent_withdrawn_or_config_changed_precedes_cache',
    'test_advisory_cache.AdvisoryCacheTests.test_changed_eligibility_secrets_coverage_and_wire_precede_cache',
    'test_advisory_cache.AdvisoryCacheTests.test_source_and_provider_binding_required_before_cache',
    'test_advisory_cache.AdvisoryCacheTests.test_context_invalidation_every_leaf',
    'test_advisory_cache.AdvisoryCacheTests.test_corruption_duplicate_extra_fields_and_citations_are_misses',
    'test_advisory_cache.AdvisoryCacheTests.test_private_no_raw_packet_paths_provider_config_or_candidate_text',
    'test_advisory_cache.AdvisoryCacheTests.test_expiry_future_clock_no_overwrite_or_deletion',
    'test_advisory_cache.AdvisoryCacheTests.test_slots_exhaustion_collision_partial_no_pruning',
    'test_advisory_cache.AdvisoryCacheTests.test_directory_overlap_missing_symlink_mode_and_ancestry',
    'test_advisory_cache.AdvisoryCacheTests.test_file_privacy_links_owner_fifo_and_directory_rejected',
    'test_advisory_cache.AdvisoryCacheTests.test_stat_open_symlink_swap_rejected',
    'test_advisory_cache.AdvisoryCacheTests.test_write_namespace_races_never_replace_other_files',
    'test_advisory_cache.AdvisoryCacheTests.test_read_replacement_and_validation_race_fail_closed',
    'test_advisory_cache.AdvisoryCacheTests.test_same_user_forgery_can_be_syntactically_valid_not_truth',
    'test_advisory_cache.AdvisoryCacheTests.test_cache_stall_in_worker_is_supervised',
    'test_advisory_cache.AdvisoryCacheTests.test_report_cache_schema_and_admission_remain_separate',
    'test_advisory_cache.AdvisoryCacheTests.test_required_cache_inventory_is_discoverable',
)


def validate_counts(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    if not isinstance(data, bytes) or len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError()
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError()
    if any(type(count) is not int or not 0 <= count <= MAX_COUNT for count in value.values()):
        raise ValueError()
    if (value['executed'] > value['expected'] or value['skipped'] > value['executed']
            or value['failed'] > value['executed'] - value['skipped']):
        raise ValueError()
    return value


def complete(value):
    return (value['expected'] > 0 and value['expected'] == value['executed']
            and value['failed'] == 0 and value['skipped'] == 0)


def summary(value):
    return ' '.join(f'{field}={value[field]}' for field in ('expected', 'executed', 'failed', 'skipped'))


def test_ids(suite):
    import unittest
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def run_suite(suite):
    import os
    import unittest

    class Counts(unittest.TestResult):
        def __init__(self):
            super().__init__()
            self.failed_tests = set()

        def addFailure(self, test, error):
            self.failed_tests.add(test.id())

        def addError(self, test, error):
            self.failed_tests.add(test.id())

        def addUnexpectedSuccess(self, test):
            self.failed_tests.add(test.id())

        def addExpectedFailure(self, test, error):
            # An expected failure is not evidence of a passing required check.
            self.failed_tests.add(test.id())

        def addSubTest(self, test, subtest, error):
            if error is not None:
                self.failed_tests.add(test.id())

    expected = suite.countTestCases()
    if not 0 < expected <= MAX_COUNT:
        raise ValueError()
    # Redirect at the descriptor boundary, including child-process output.
    saved = [os.dup(descriptor) for descriptor in (1, 2)]
    try:
        with open(os.devnull, 'w') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            result = Counts()
            suite.run(result)
    finally:
        for descriptor, original in zip((1, 2), saved):
            os.dup2(original, descriptor)
            os.close(original)
    value = {'expected': expected, 'executed': result.testsRun,
             'failed': len(result.failed_tests), 'skipped': len(result.skipped)}
    return validate_counts(json.dumps(value).encode())
