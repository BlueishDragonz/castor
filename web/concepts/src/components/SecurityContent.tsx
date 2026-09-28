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

/**
 * Security page content — React component for client-side interactivity
 * Matches main branch security_page.py
 */
export function SecurityContent() {
  const [passkeys, setPasskeys] = useState<Passkey[]>([]);
  const [recoveryEmail, setRecoveryEmail] = useState<RecoveryEmail | null>(null);
  const [loading, setLoading] = useState(true);

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
        setRecoveryEmail(await recoveryRes.json());
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
      alert('Password must be at least 12 characters');
      return;
    }
    
    const confirmPassword = prompt('Confirm new password:');
    if (newPassword !== confirmPassword) {
      alert('Passwords do not match');
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
        alert('Password changed successfully');
      } else {
        const data = await res.json();
        alert(data.detail || 'Failed to change password');
      }
    } catch (e: any) {
      alert('Error: ' + e.message);
    }
  }

  async function handleAddRecoveryEmail() {
    const email = prompt('Enter recovery email:');
    if (!email || !email.includes('@')) {
      alert('Please enter a valid email');
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
        alert('Recovery email added. Please check your inbox to verify.');
        fetchData();
      } else {
        const data = await res.json();
        alert(data.detail || 'Failed to add recovery email');
      }
    } catch (e: any) {
      alert('Error: ' + e.message);
    }
  }

  async function handleVerifyEmail() {
    const code = prompt('Enter verification code:');
    if (!code) return;

    try {
      const res = await fetch('/api/v1/webauthn/recovery-email/verify', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });

      if (res.ok) {
        alert('Email verified successfully');
        fetchData();
      } else {
        const data = await res.json();
        alert(data.detail || 'Invalid code');
      }
    } catch (e: any) {
      alert('Error: ' + e.message);
    }
  }

  async function handleRemovePasskey(passkeyId: string) {
    if (!confirm('Remove this passkey? You will not be able to use it to sign in.')) return;

    try {
      const res = await fetch(
        `/api/v1/webauthn/credentials/${encodeURIComponent(passkeyId)}`,
        { method: 'DELETE', credentials: 'same-origin' },
      );

      if (res.ok) {
        alert('Passkey removed');
        fetchData();
      } else {
        const data = await res.json();
        alert(data.detail || 'Failed to remove passkey');
      }
    } catch (e: any) {
      alert('Error: ' + e.message);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-6 space-y-4 pb-24">
        <div className="text-center text-muted-foreground">Loading...</div>
      </div>
    );
  }

  return (
    <>
      {/*
        Slice 22: removed the SecurityContent-local `<header>` (which
        duplicated the page title) and the inner `<main>` wrapper
        (the Astro page now provides one). Renders only the cards.
      */}

      {/* Passkeys Section */}
      <Card className="w-full">
        <CardHeader className="pb-2 flex items-center justify-between">
          <CardTitle as="h2" className="text-base font-medium flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Passkeys
          </CardTitle>
          <Button variant="outline" size="sm" className="gap-1">
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
            </div>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Add a recovery email to regain access if you lose your passkeys.
              </p>
              <Button variant="outline" className="w-full" onClick={handleAddRecoveryEmail}>
                Add Recovery Email
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      {/* 2FA Status */}
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
              Passkeys provide passwordless, phishing-resistant authentication. 
              When you have at least one passkey registered, 2FA is effectively enabled.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    </>
  );
}