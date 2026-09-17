/* Security-page registration only. Authentication remains in HttpOnly cookies. */
(() => {
    'use strict';

    // NiceGUI may inject the same head content again. Keep the original closure,
    // especially its active operation, rather than permitting a second ceremony.
    if (typeof window.registerSecurityPasskey === 'function') return;

    let active = null;
    const cancelled = () => ({status: 'cancelled'});
    const failure = message => ({status: 'error', message});
    const uncertain = () => ({
        status: 'uncertain',
        message: 'Registration may have completed. Refresh and check your passkey list before trying again.',
    });

    function decode(value) {
        const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
        const padded = base64 + '='.repeat((4 - base64.length % 4) % 4);
        return Uint8Array.from(atob(padded), character => character.charCodeAt(0));
    }

    function encode(value) {
        const bytes = ArrayBuffer.isView(value)
            ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
            : new Uint8Array(value);
        // Do not spread a potentially large attestation onto the argument stack.
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function post(body) {
        return {
            method: 'POST',
            credentials: 'same-origin',
            mode: 'same-origin',
            redirect: 'error',
            cache: 'no-store',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        };
    }

    function httpFailure(response, stage) {
        if (response.status === 401 || response.status === 403) {
            return failure('Your session could not authorize registration. Sign in again and retry.');
        }
        // Deliberately do not echo arbitrary response bodies or exception text.
        return failure(`Could not ${stage} passkey registration (HTTP ${response.status}).`);
    }

    window.cancelSecurityPasskey = () => {
        // Once submitted, aborting the transport cannot undo a server commit.
        // Continue awaiting the outcome and retain the lock until it is known.
        if (active && !active.submitted) active.controller.abort();
    };

    window.registerSecurityPasskey = async (email, nickname) => {
        if (active) return failure('A passkey registration is already in progress.');
        if (!window.isSecureContext) {
            return failure('Passkeys need a secure connection. Open this page using HTTPS.');
        }
        if (typeof window.PublicKeyCredential !== 'function'
            || !window.navigator?.credentials
            || typeof window.navigator.credentials.create !== 'function'
            || typeof window.AbortController !== 'function') {
            return failure('This browser does not support passkey registration. Try a supported browser.');
        }
        if (typeof email !== 'string' || !email.trim()) {
            return failure('Sign in again before adding a passkey.');
        }
        const name = typeof nickname === 'string' ? nickname.trim() : '';
        // Count Unicode code points, matching the server's Python string limit;
        // UTF-16 .length would incorrectly count an emoji as two characters.
        if (!name || Array.from(name).length > 64) {
            return failure('Enter a passkey name between 1 and 64 characters.');
        }

        const operation = {controller: new AbortController(), submitted: false};
        const signal = operation.controller.signal;
        active = operation;
        try {
            const beginResponse = await fetch('/auth/webauthn/register/begin', {
                ...post({username: email}), signal,
            });
            if (signal.aborted) return cancelled();
            if (!beginResponse.ok) return httpFailure(beginResponse, 'start');
            const data = await beginResponse.json();
            if (signal.aborted) return cancelled();
            const publicKey = data.publicKey;
            publicKey.challenge = decode(publicKey.challenge);
            publicKey.user.id = decode(publicKey.user.id);
            if (publicKey.excludeCredentials) {
                publicKey.excludeCredentials = publicKey.excludeCredentials.map(item => ({
                    ...item, id: decode(item.id),
                }));
            }
            const credential = await window.navigator.credentials.create({publicKey, signal});
            // Some platform implementations resolve after an abort; never submit
            // their late result, even if create() ignored the signal.
            if (signal.aborted || !credential) return cancelled();
            const request = post({
                username: email,
                name,
                id: credential.id,
                rawId: encode(credential.rawId),
                type: credential.type,
                response: {
                    attestationObject: encode(credential.response.attestationObject),
                    clientDataJSON: encode(credential.response.clientDataJSON),
                    transports: credential.response.getTransports?.() || [],
                },
            });
            operation.submitted = true;
            const completeResponse = await fetch('/auth/webauthn/register/complete', request);
            if (!completeResponse.ok) return httpFailure(completeResponse, 'complete');
            const result = await completeResponse.json();
            // The legacy server signals acceptance with access_token and also
            // sets its HttpOnly cookie. Recognize the protocol, but never expose
            // this object/token to Python, logs, browser storage, or UI state.
            if (typeof result?.access_token === 'string' && result.access_token.length > 0) {
                return {status: 'success'};
            }
            return uncertain();
        } catch (error) {
            // Includes failed completion-body reads: the server may have saved
            // the passkey already. Never automatically replay begin/complete.
            if (operation.submitted) return uncertain();
            if (signal.aborted || error?.name === 'AbortError' || error?.name === 'NotAllowedError') {
                return cancelled();
            }
            if (error?.name === 'InvalidStateError') {
                return failure('This authenticator may already have a passkey for your account. Try another authenticator.');
            }
            return failure('Could not register a passkey. Check your connection and try again.');
        } finally {
            active = null;
        }
    };
})();
