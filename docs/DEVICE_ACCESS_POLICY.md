# Trusted Device Access Policy — AI Ops Universe

## Target policy
- The user's enrolled primary phone receives the `admin` role.
- The user's enrolled primary PC receives the `privileged` role.
- Other accounts/devices remain at their separately assigned account role (normally `ordinary`).
- `admin` and `privileged` are separate roles. Admin cannot approve a privileged-role request.
- A device name, user-agent, browser fingerprint, or IP address is not proof of identity.

## Required implementation before enabling this policy
The current backend authenticates roles at the **account** level. It does not bind sessions to trusted devices. Do not treat this policy as active until all steps below are implemented.

1. Add a server-side `trusted_devices` table with a random device ID, owner user ID, device role (`admin` or `privileged`), created/last-seen/revoked timestamps, and a non-reversible token hash. Never store a raw enrollment secret.
2. Enroll the phone and PC separately through a one-time server-side enrollment challenge, after re-authentication and a second factor. Never put enrollment secrets in GitHub Pages, HTML, query strings, or logs.
3. Bind each issued session to its enrolled device record. Derive the effective role from the valid device binding on the server, not from a role sent by the browser.
4. Require re-authentication and a separate privileged approval for enrollment/re-enrollment of the PC as `privileged`. The phone's Admin authority must not be able to grant itself Privileged.
5. Add a revoke-device action, session revocation on device revoke, audit events, expiry/rotation, and recovery if the PC or phone is lost.
6. Test that unknown devices cannot obtain elevated roles; phone gets Admin only; PC gets Privileged only; ordinary devices remain ordinary; revoked devices lose access; Admin cannot grant Privileged; and forged browser labels/roles are ignored.
7. Keep the one-time bootstrap token server-side and remove/rotate it after the initial Admin account is provisioned.

## Current status
**Specification only — not implemented.** Current `/auth/login` returns the user's account role, and there is no trusted-device enrollment or device-bound session. Do not infer device identity from IP address or a client-side device label. GitHub Pages is a static frontend, not the authority for access control.
