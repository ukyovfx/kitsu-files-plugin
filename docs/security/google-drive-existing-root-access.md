# Least-privilege access to existing Google Drive roots

The Plugin currently requests `openid` and `drive.file`. It must not silently
increase its OAuth scope to discover an arbitrary existing Production folder.
Google describes `drive.file` as access to files created by the app, opened by
the app, or shared with the app through Google Picker or the app's file picker.
The scope guide recommends Picker when an app needs the user to select
pre-existing Drive content ([Drive API scope guide](https://developers.google.com/workspace/drive/api/guides/api-specific-auth),
[Picker web guide](https://developers.google.com/workspace/drive/picker/guides/web-picker)).

## Supported choices to evaluate

| Workflow | How the Plugin obtains root access | Least-privilege fit | Constraint |
|---|---|---|---|
| Picker selection handoff | An authorized user selects the exact existing folder in Google Picker. The server then re-reads the selected folder with its own authorized connection and binds the verified Drive ID to one Production. | Retains `drive.file`; user grants access to the selected item rather than all Drive files. | The official web Picker requires an OAuth access token in its browser integration. That conflicts with this Plugin's current rule that Google OAuth tokens never enter browser JavaScript. Do not implement until the owner approves a revised token boundary or a secure Picker handoff design. Picker selection is an access grant, not proof that the selected folder is the intended Production root; server-side parent, drive, owner and capability checks remain mandatory. |
| Plugin-owned dedicated root | The server creates a new Production root through the authorized connection and records its returned Drive ID. Future folders/files are created beneath that app-created root. | Fits app-created-object access under `drive.file` and keeps tokens server-side. | Does not adopt an existing tree automatically. A human must choose this workflow and decide how existing media is brought in; no implicit copy, move, or reparenting is allowed. Shared Drive support still requires a separately verified Drive identity and permission checks. |

## Decision boundary

Until a security decision is made, existing-root enumeration and root adoption
remain blocked. The Plugin must not request `drive` or `drive.readonly` as a
fallback. Those are restricted scopes with additional verification/security
assessment implications in Google's scope guide. FakeStorage and synthetic
identity tests do not establish real Drive compatibility.

No Google consent or Drive API request was made while preparing this note.
