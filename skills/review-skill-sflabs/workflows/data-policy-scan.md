# Data policy check implementation

Reference documentation only. The trusted deterministic validator scans every
supported text file in its bounded snapshot, including references and script text.
It does not sample files or execute scripts. Unsupported formats make coverage
incomplete; archives are not expanded.

Known-secret patterns block validation and packet creation. No matched values or
candidate prose appear in diagnostic output. Detection is best effort and may
have false positives and false negatives. Internal collection of personal data
is not automatically authorized merely because it is hidden from output.

Identity fields, environment-specific names, tone and disclaimer patterns are
advisory flags for contextual human review, not proof of inappropriate behavior.
Optional contextual model review is disabled in this release. Do not perform it
in the host agent or open candidate files to expand findings.
