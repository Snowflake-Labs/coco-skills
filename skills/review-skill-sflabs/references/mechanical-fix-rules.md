# No automatic fixes

The contained reviewer never changes the candidate. Older versions offered edits,
license creation and default metadata insertion; those actions are removed.

Diagnostics use stable check IDs and fixed explanations. The human author decides
what to change outside this workflow. Neither candidate text nor model advice can
authorize edits, commands, uploads, or expanded filesystem access.

Metadata and license decisions use the single [policy reference](../workflows/format-check.md).
In particular, do not insert a contributor type, overwrite author aliases or copy
a source copyright notice to silence a diagnostic. Unmatched license text needs
maintainer review; content matching is not legal approval.
