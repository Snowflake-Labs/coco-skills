# EARS Notation Reference

**EARS** = Easy Approach to Requirements Syntax

A structured way to write unambiguous, testable requirements.

## Basic Pattern

```
WHEN [trigger/condition]
THE SYSTEM SHALL [observable response]
SO THAT [business value/rationale]
```

## Requirement Types

### 1. Ubiquitous (Always Active)

Requirements that are always true, with no specific trigger.

```markdown
THE SYSTEM SHALL [behavior]
```

**Example**:
```
THE SYSTEM SHALL encrypt all user passwords using bcrypt with cost factor 12.
```

### 2. Event-Driven (Response to Action)

Requirements triggered by a specific event or action.

```markdown
WHEN [event occurs]
THE SYSTEM SHALL [response]
```

**Example**:
```
WHEN a user clicks the "Submit" button
THE SYSTEM SHALL validate all form fields and display errors for invalid inputs.
```

### 3. State-Driven (Conditional on State)

Requirements that apply only when system is in a certain state.

```markdown
WHILE [in state]
THE SYSTEM SHALL [behavior]
```

**Example**:
```
WHILE the user session is active
THE SYSTEM SHALL refresh the authentication token every 15 minutes.
```

### 4. Optional (Feature Flags)

Requirements that may be enabled/disabled.

```markdown
WHERE [feature is enabled]
THE SYSTEM SHALL [behavior]
```

**Example**:
```
WHERE dark mode is enabled
THE SYSTEM SHALL render all UI components with the dark color palette.
```

### 5. Complex (Combined Conditions)

Requirements with multiple conditions.

```markdown
WHILE [state] WHEN [event]
IF [condition] THE SYSTEM SHALL [response]
```

**Example**:
```
WHILE the user is logged in
WHEN the user attempts to access a premium feature
IF the user has a free account
THE SYSTEM SHALL display the upgrade prompt modal.
```

## Negative Requirements

Use "SHALL NOT" for prohibited behaviors.

```markdown
THE SYSTEM SHALL NOT [prohibited behavior]
```

**Example**:
```
THE SYSTEM SHALL NOT store credit card CVV codes after transaction completion.
```

## Template for Cortex Code Specs

```markdown
### REQ-{XXX}: {Descriptive Title}

**Type**: [Ubiquitous | Event-Driven | State-Driven | Optional | Complex]

**Priority**: [Must Have | Should Have | Could Have | Won't Have]

**Statement**:
WHEN {trigger condition or event}
THE SYSTEM SHALL {measurable, observable behavior}
SO THAT {business value or user benefit}

**Acceptance Criteria**:
- [ ] {Testable criterion 1}
- [ ] {Testable criterion 2}
- [ ] {Testable criterion 3}

**Notes**: {Any additional context, edge cases, or clarifications}
```

## Best Practices

### DO:
- Use active voice ("THE SYSTEM SHALL display" not "will be displayed")
- Be specific and measurable ("within 2 seconds" not "quickly")
- Include one requirement per statement
- Make each requirement independently testable
- Include the business rationale (SO THAT clause)

### DON'T:
- Use vague terms ("user-friendly", "fast", "intuitive")
- Combine multiple requirements in one statement
- Use "should" or "may" - use "SHALL" for requirements
- Write implementation details - focus on WHAT not HOW
- Forget edge cases and error scenarios

## Examples by Domain

### API Endpoint
```
WHEN a client sends a GET request to /api/users/{id}
IF the user exists
THE SYSTEM SHALL return a 200 response with the user JSON object
SO THAT clients can retrieve user information.
```

### Error Handling
```
WHEN a database connection fails
THE SYSTEM SHALL retry the connection 3 times with exponential backoff
AND IF all retries fail, return a 503 Service Unavailable response
SO THAT transient failures don't cause immediate user-facing errors.
```

### UI Behavior
```
WHEN the user submits a form with invalid email format
THE SYSTEM SHALL display an inline error message "Please enter a valid email address"
AND SHALL NOT submit the form to the server
SO THAT users get immediate feedback on validation errors.
```

### Security
```
WHEN a user fails login 5 times within 10 minutes
THE SYSTEM SHALL lock the account for 30 minutes
AND SHALL send a security alert email to the user
SO THAT brute force attacks are mitigated.
```
