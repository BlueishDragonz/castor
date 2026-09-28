'use client';

import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Shield, Key, Mail, Lock, Trash2, Plus } from 'lucide-react';

interface Passkey {
  id: string;
  name: string;
  created_at: string;
  last_used: string | null;
}

interface RecoveryEmail {
  email: string;
  verified: boolean;
}

type StatusMessage = { kind: 'ok' | 'error'; text: string } | null;

/**
 * Security page content — React component for client-side interactivity
 * Matches main branch security_page.py
 */
export function SecurityContent({ email }: { email: string }) {
  const [passkeys, setPasskeys] = useState<Passkey[]>([]);
  const [recoveryEmail, setRecoveryEmail] = useState<RecoveryEmail | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<StatusMessage>(null);

  /**
   * Turn a BFF error response into one human-readable string.
   *
   * FastAPI validation failures return `detail` as an ARRAY of objects, and
   * backend auth failures return a string. Rendering `[object Object]` at the
   * user — which is what the previous `alert(data.detail)` did for every
   * 422 — tells them nothing about what went wrong.
   */
  async function readError(res: Response, fallback: string): Promise<string> {
    const data = await res.json().catch(() => null);
    if (typeof data?.detail === 'string') return data.detail;
    if (Array.isArray(data?.detail) && data.detail.length > 0) {
      const first = data.detail[0];
      if (typeof first?.msg === 'string') return first.msg;
    }
    return fallback;
  }

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    try {
      // F7: these go through the same-origin BFF, which attaches the bearer
      // from the httpOnly cookie server-side. The session JWT is therefore
      // never readable by client JavaScript — previously this component read
      // it from `window.__SECURITY_TOKEN__`, which made the httpOnly cookie
      // design decorative and turned any XSS into a 30-day token theft.
      //
      // Relative URLs also keep the browser same-origin in both dev and
      // production, so no CORS grant is needed from the backend.
      const [passkeysRes, recoveryRes] = await Promise.all([
        fetch('/api/v1/webauthn/credentials', { credentials: 'same-origin' }),
        fetch('/api/v1/webauthn/recovery-email', { credentials: 'same-origin' }),
      ]);

      if (passkeysRes.ok) {
        setPasskeys(await passkeysRes.json());
      }
      if (recoveryRes.ok) {
        // The backend returns {recovery_email, recovery_email_verified, pending_email}.
        // The component's own type called them {email, verified}, so the
        // fields read as undefined and the "you have no recovery address"
        // panel rendered even when one was set and verified.
        //
        // `pending_email` matters: `user.recovery_email` is only written
        // when the 6-digit code is verified, so between proposing an
        // address and pasting the code the stored field is still null.
        // Showing only the stored value means the Verify button — the one
        // control that completes the flow — never appears, while the code
        // sits unread in the user's inbox.
        const data = await recoveryRes.json().catch(() => null);
        const address = data?.recovery_email ?? data?.pending_email ?? null;
        if (address) {
          setRecoveryEmail({
            email: address,
            verified: Boolean(data?.recovery_email_verified),
          });
        } else {
          setRecoveryEmail(null);
        }
      }
    } catch (e) {
      console.error('Failed to load security data:', e);
    } finally {
      setLoading(false);
    }
  }

  async function handleChangePassword() {
    const currentPassword = prompt('Current password:');
    if (!currentPassword) return;
    
    const newPassword = prompt('New password (min 12 chars):');
    if (!newPassword || newPassword.length < 12) {
      setMessage({ kind: 'error', text: 'Password must be at least 12 characters.' });
      return;
    }

    const confirmPassword = prompt('Confirm new password:');
    if (newPassword !== confirmPassword) {
      setMessage({ kind: 'error', text: 'The two passwords do not match.' });
      return;
    }

    try {
      const res = await fetch('/api/v1/webauthn/change-password', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });

      if (res.ok) {
        // The backend bumps token_version on success, which retires every
        // outstanding JWT for this account — including the one backing the
        // current session cookie. Continuing in place would leave the user on
        // a page whose every subsequent write 401s, so send them to re-auth.
        setMessage({ kind: 'ok', text: 'Password changed. Please sign in again.' });
        window.setTimeout(() => {
          window.location.href = '/login?expired=1';
        }, 1200);
      } else {
        setMessage({ kind: 'error', text: await readError(res, 'Could not change the password.') });
      }
    } catch (e: any) {
      setMessage({ kind: 'error', text: 'Error: ' + e.message });
    }
  }

  async function handleAddRecoveryEmail() {
    const email = prompt('Enter recovery email:');
    if (!email || !email.includes('@')) {
      setMessage({ kind: 'error', text: 'That does not look like an email address.' });
      return;
    }

    try {
      const res = await fetch('/api/v1/webauthn/recovery-email', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });

      if (res.ok) {
        setMessage({
          kind: 'ok',
          text: 'Recovery email added. Check your inbox for a 6-digit code, then use Verify.',
        });
        fetchData();
      } else {
        setMessage({ kind: 'error', text: await readError(res, 'Could not add the recovery email.') });
      }
    } catch (e: any) {
      setMessage({ kind: 'error', text: 'Error: ' + e.message });
    }
  }

  async function handleVerifyEmail() {
    if (!recoveryEmail) return;
    const code = prompt('Enter the 6-digit verification code:');
    if (!code) return;

    try {
      const res = await fetch('/api/v1/webauthn/recovery-email/verify', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        // `email` is REQUIRED by the backend (RecoveryEmailVerifyRequest) and
        // must match the address the code was sent to. Omitting it produced a
        // 422 and the Verify button could never succeed.
        body: JSON.stringify({ email: recoveryEmail.email, code }),
      });

      if (res.ok) {
        setMessage({ kind: 'ok', text: 'Recovery email verified.' });
        fetchData();
      } else {
        setMessage({ kind: 'error', text: await readError(res, 'That code is not valid.') });
      }
    } catch (e: any) {
      setMessage({ kind: 'error', text: 'Error: ' + e.message });
    }
  }

  async function handleRemovePasskey(passkeyId: string) {
    if (!confirm('Remove this passkey? You will not be able to use it to sign in.')) return;

    // The backend gates deletion on a fresh password (PasskeyDeleteRequest),
    // NOT just the session. The 30-day session is not sufficient authority
    // to destroy a second factor, so this prompt is required, not optional:
    // sending the DELETE without it produced a 422 "Field required: body"
    // and the button silently did nothing.
    const currentPassword = prompt('Confirm your current password to remove this passkey:');
    if (!currentPassword) return;

    try {
      const res = await fetch(
        `/api/v1/webauthn/credentials/${encodeURIComponent(passkeyId)}`,
        {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: currentPassword }),
        },
      );

      if (res.ok) {
        setMessage({ kind: 'ok', text: 'Passkey removed.' });
        fetchData();
      } else {
        const data = await res.json().catch(() => null);
        const detail = typeof data?.detail === 'string'
          ? data.detail
          : 'Could not remove the passkey. Check your password and try again.';
        setMessage({ kind: 'error', text: detail });
      }
    } catch (e: any) {
      setMessage({ kind: 'error', text: 'Error: ' + e.message });
    }
  }

  async function handleAddPasskey() {
    setMessage(null);
    try {
      // Same two-step ceremony the /login page's post-login offer runs, and
      // the same BFF pair the /register page uses: begin (server, needs the
      // session bearer) → navigator.credentials.create → complete.
      //
      // The "Add" button previously had NO onClick at all, so the only way to
      // enrol a passkey outside the one-shot post-login offer was... there
      // wasn't one. The page has been the advertised home for passkey
      // management, and it could list and delete but not add.
      const beginRes = await fetch('/api/auth/webauthn/register/begin', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: email }),
      });
      if (!beginRes.ok) {
        setMessage({ kind: 'error', text: await readError(beginRes, 'Could not start passkey setup.') });
        return;
      }
      const { publicKey: pk } = await beginRes.json();

      const bufferOf = (value: string): ArrayBuffer => {
        const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
        const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
        const binary = atob(padded);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        return bytes.buffer;
      };
      const toBase64Url = (buf: ArrayBuffer): string => {
        let binary = '';
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
      };

      pk.challenge = bufferOf(pk.challenge);
      pk.user.id = bufferOf(pk.user.id);
      if (pk.excludeCredentials) {
        pk.excludeCredentials = pk.excludeCredentials.map((c: any) => ({ ...c, id: bufferOf(c.id) }));
      }

      const credential = await navigator.credentials.create({ publicKey: pk });
      if (!(credential instanceof PublicKeyCredential)) {
        throw new Error('No passkey was created.');
      }
      const attestation = credential.response;
      if (!(attestation instanceof AuthenticatorAttestationResponse)) {
        throw new Error('Unexpected credential response type.');
      }

      const completeRes = await fetch('/api/auth/webauthn/register/complete', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: credential.id,
          rawId: toBase64Url(credential.rawId),
          response: {
            clientDataJSON: toBase64Url(attestation.clientDataJSON),
            attestationObject: toBase64Url(attestation.attestationObject),
            transports: (attestation as any).getTransports?.() ?? undefined,
          },
          type: credential.type,
          // The backend binds both register endpoints to the caller's own
          // address (`_self_binding`) — this is what stops one user from
          // enrolling a credential on another user's account. It must be the
          // session email exactly, not a guess.
          username: email,
          name: 'Passkey',
        }),
      });
      if (!completeRes.ok) {
        setMessage({ kind: 'error', text: await readError(completeRes, 'Could not save the passkey.') });
        return;
      }
      setMessage({ kind: 'ok', text: 'Passkey added.' });
      fetchData();
    } catch (e: any) {
      // Platform conditions are not failures the user did anything wrong
      // about. Say so plainly instead of showing a raw DOMException name.
      if (e?.name === 'NotAllowedError') {
        setMessage({ kind: 'error', text: 'No worries — you can try again whenever you are ready.' });
      } else if (e?.name === 'InvalidStateError') {
        setMessage({ kind: 'error', text: 'This device already has a passkey for Castor.' });
      } else {
        setMessage({ kind: 'error', text: e?.message ?? 'Could not add the passkey.' });
      }
    }
  }

  if (loading) {
    return (
      <div className="text-center text-muted-foreground py-6">Loading...</div>
    );
  }

  return (
    <div className="castor-stack">
      {/*
        Slice 22: removed the SecurityContent-local `<header>` (which
        duplicated the page title) and the inner `<main>` wrapper
        (the Astro page now provides one). Renders only the cards.

        The wrapper that replaced it is `castor-stack`, the same
        1.25rem vertical rhythm /stats and /settings use. Previously the
        cards were bare siblings inside a fragment, so they became
        direct children of <main> and rendered with **0px** between
        them — the passkeys, recovery-email and 2FA cards shared an edge
        and read as one merged block.
      */}

      {/*
        One status region for every action on this page.

        It replaces the per-handler `alert()` calls. `alert()` is a blocking
        modal that a screen reader announces as a separate window, gives no
        lasting record of what happened, and — because it fires from an async
        handler after the DOM has settled — is easy to miss entirely. A live
        region keeps the outcome on the page, announced politely, and is
        focusable so keyboard users can reach it.
      */}
      <p
        role="status"
        aria-live="polite"
        className={
          message
            ? `text-sm ${message.kind === 'error' ? 'text-destructive' : 'text-muted-foreground'}`
            : 'sr-only'
        }
      >
        {message?.text ?? ''}
      </p>

      {/*
        Two-factor authentication comes first.
        It is the single answer to "is my account protected?", which is
        the question a visitor to /security arrives with, and it is the
        only panel here that states a verdict. The passkey list and the
        recovery email are the configuration detail behind that verdict,
        so they follow it. It was previously last, below two setup
        panels a user has to read past to reach the summary.
      */}
      <Card className="w-full">
        <CardHeader className="pb-2">
          <CardTitle as="h2" className="text-base font-medium flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Two-Factor Authentication
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <Alert variant="default">
            <Shield className="h-4 w-4" />
            <AlertDescription className="text-sm">
              Passkeys provide passwordless, phishing-resistant authentication.{' '}
              When you have at least one passkey registered, 2FA is effectively enabled.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>

      {/* Passkeys Section */}
      <Card className="w-full">
        <CardHeader className="pb-2 flex items-center justify-between">
          <CardTitle as="h2" className="text-base font-medium flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Passkeys
          </CardTitle>
          <Button variant="outline" size="sm" className="gap-1" onClick={handleAddPasskey}>
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </CardHeader>
        <CardContent className="pt-0 space-y-3">
          {passkeys.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No passkeys registered. Add a passkey for passwordless sign-in.
            </p>
          ) : (
            <div className="space-y-2">
              {passkeys.map(passkey => (
                <div key={passkey.id} className="flex items-center justify-between p-3 border border-border rounded-lg">
                  <div className="flex items-center gap-3">
                    <Key className="h-5 w-5 text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium">{passkey.name || 'Security Key'}</p>
                      <p className="text-xs text-muted-foreground">
                        Added {new Date(passkey.created_at).toLocaleDateString('en-GB', { 
                          day: 'numeric', month: 'short', year: 'numeric' })}
                        {passkey.last_used ? ` · Last used ${new Date(passkey.last_used).toLocaleDateString('en-GB', { 
                          day: 'numeric', month: 'short', year: 'numeric' })}` : ''}
                      </p>
                    </div>
                  </div>
                  <Button 
                    variant="ghost" 
                    size="icon" 
                    className="text-destructive hover:text-destructive"
                    onClick={() => handleRemovePasskey(passkey.id)}
                    aria-label={`Remove ${passkey.name || 'passkey'}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Password Section */}
      <Card className="w-full">
        <CardHeader className="pb-2 flex items-center justify-between">
          <CardTitle as="h2" className="text-base font-medium flex items-center gap-2">
            <Lock className="h-5 w-5" />
            Password
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0 space-y-3">
          <p className="text-sm text-muted-foreground">
            Change your password. You'll need to verify your current password first.
          </p>
          <Button variant="outline" className="w-full" onClick={handleChangePassword}>
            Change Password
          </Button>
        </CardContent>
      </Card>

      {/* Recovery Email Section */}
      <Card className="w-full">
        <CardHeader className="pb-2 flex items-center justify-between">
          <CardTitle as="h2" className="text-base font-medium flex items-center gap-2">
            <Mail className="h-5 w-5" />
            Recovery Email
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0 space-y-3">
          {/*
            The copy here used to be a single line — "Add a recovery
            email to regain access if you lose your passkeys" — which
            implied recovery email was off until you turned it on, and
            said nothing about what happens to the address you already
            have. Both were wrong in a way that matters at the moment
            someone actually needs recovery: the default recovery
            address IS the registration email, it is always in force, and
            adding a second one does not replace it as the sign-in
            address. So the panel now states the default first, then the
            opt-in, then the warning.
          */}
          {recoveryEmail ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between p-3 border border-border rounded-lg">
                <div className="flex items-center gap-3">
                  <Mail className="h-5 w-5 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">{recoveryEmail.email}</p>
                    <p className="text-xs text-muted-foreground">
                      {recoveryEmail.verified ? 'Verified' : 'Not verified'}
                    </p>
                  </div>
                </div>
                {!recoveryEmail.verified && (
                  <Button variant="outline" size="sm" className="gap-1" onClick={handleVerifyEmail}>
                    Verify
                  </Button>
                )}
              </div>
              <Alert variant="default">
                <AlertDescription className="text-sm">
                  This is an additional address for recovery messages only.
                  You still sign in with your registration email.
                </AlertDescription>
              </Alert>
            </div>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Your recovery address is the email you registered with, and
                it is already active — recovery messages go there by default.
              </p>
              <p className="text-sm text-muted-foreground">
                Add a different address below if you want recovery messages
                to reach somewhere else as well.
              </p>
              <Alert variant="default">
                <AlertDescription className="text-sm">
                  This only changes where recovery messages are sent. You
                  will still need your registration email to sign in, and
                  your passkeys are unchanged.
                </AlertDescription>
              </Alert>
              <Button variant="outline" className="w-full" onClick={handleAddRecoveryEmail}>
                Add Recovery Email
              </Button>
            </>
          )}
        </CardContent>
      </Card>

    </div>
  );
}