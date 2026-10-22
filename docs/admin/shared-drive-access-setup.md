# Shared Drive access setup (studio admin checklist)

Output Files opens Output versions **in Google Drive**. Google (Drive ACL) decides who can view them; the Plugin does not
serve file bytes in real-Drive mode and never creates sharing. Kitsu permissions control the *Plugin's* actions
(delete, register, settings); Drive viewing is controlled only in Google. The Plugin cannot read Google-side settings
with its `drive.file` grant, so the status panel marks them "Not verified" - confirm them yourself.

Use a **Google Workspace shared drive**. A My Drive shared folder is a different structure (no shared-drive members or
restrictions); the panel flags it as "Check".

## Steps (Google Workspace admin / shared-drive Manager)
1. **Group**: create a Google Group for the people who may view this Production's files (e.g. `prod-xyz-viewers`).
   Add and remove members in Google; Kitsu members are **not** synced to the group automatically.
2. **Shared drive membership**: add the group to the shared drive as **Viewer** (or Commenter). Give editing roles only
   to people who need them.
3. **Members only**: in the shared drive settings turn off adding files to people who are not members and
   turn off access for people outside your organization, unless you deliberately allow external accounts
   (admin console: Apps > Google Workspace > Drive and Docs > Sharing settings > Shared drives; per-drive settings via
   Manage shared drives). API name for the restriction: `driveMembersOnly`.
4. **Viewer downloads**: if Viewers must not download/print/copy, turn off "Allow viewers and commenters to
   download, print, and copy files".
5. **External Google accounts**: only if allowed in step 3; add the individual accounts (or an external-friendly group)
   explicitly. Behaviour for personal Gmail accounts depends on your organization's settings - test before relying on it.
6. **Plugin credential**: the Google account connected for the Production must be a **Manager** of the shared drive if
   you want permanent deletion to work (Content managers can only trash). Without it, deletion fails closed.
7. **Never** use "Anyone with the link". The Plugin does not create such links; do not add them manually.

## What to verify in a test Workspace before go-live
Member vs non-member vs external account opening a file link; removal from the group; Viewer download restriction;
a file shared by link attempt. These are listed as pending real-environment checks (design doc, section 6).
