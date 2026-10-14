# SPRKPY1092

At least one statement with backslash was removed, a manual backslash addition may be needed.

Message: At least one statement with backslash was removed, a manual backslash addition may be needed.

Category: Warning

## Description

This issue appears when the SMA removes backslashes during code conversion, typically in multiline statements or string continuations. The converted code may need manual review to ensure proper line continuation syntax is maintained.

## Resolution

This is an informational warning. **Review the converted code** and verify that multiline statements are correctly formatted. If needed, manually add backslashes for line continuation.

## Additional recommendations

- Check multiline statements in the converted code to ensure they maintain correct Python syntax
- Verify that string literals and function calls spanning multiple lines are properly formatted
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
