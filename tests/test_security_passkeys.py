"""Execute the production helper in Node with isolated browser/HTTP doubles.

These are client-contract tests, not cryptographic authenticator verification.
No network requests or real accounts are used.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "beaverhabits/frontend/security_passkeys.js"

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scenario = process.argv[1];
const source = fs.readFileSync(process.argv[2], 'utf8');
const events = [];
const calls = [];
const created = [];
const credential = () => ({
    id: 'credential-id', type: 'public-key',
    rawId: Uint8Array.from([251, 255, 0]).buffer,
    response: {
        attestationObject: Uint8Array.from([0, 255, 254]).buffer,
        clientDataJSON: Uint8Array.from([123, 125]).buffer,
        getTransports: () => ['internal', 'hybrid'],
    },
});
const options = () => ({publicKey: {
    challenge: '-_8A', user: {id: 'AQI', name: 'person@example.test'},
    rp: {name: 'Test', id: 'example.test'},
    pubKeyCredParams: [{type: 'public-key', alg: -7}],
    excludeCredentials: [{id: 'AP_-', type: 'public-key', transports: ['usb']}],
}});
const response = (body, status = 200) => ({
    ok: status >= 200 && status < 300, status,
    json: async () => body,
});
const nonJSON = (status) => ({ok: status === 200, status, json: async () => {
    throw new SyntaxError('invalid response');
}});
const deferred = () => {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return {promise, resolve};
};
const tick = () => new Promise(resolve => setImmediate(resolve));
let doCreate = async () => credential();
let doFetch = async url => url.endsWith('/begin')
    ? response(options()) : response({access_token: 'test-only-protocol-marker'});
const context = {
    window: null, isSecureContext: true, PublicKeyCredential: function () {},
    AbortController, Uint8Array, ArrayBuffer, DOMException,
    atob: s => Buffer.from(s, 'base64').toString('binary'),
    btoa: s => Buffer.from(s, 'binary').toString('base64'),
    navigator: {credentials: {create: async arg => {
        created.push(arg); return doCreate(arg);
    }}},
    fetch: async (url, init) => { calls.push({url, init}); return doFetch(url, init); },
    console: Object.fromEntries(['log','error','warn','info','debug'].map(k => [k, (...v) => events.push(v)])),
    localStorage: {setItem: (...v) => events.push(v)},
    sessionStorage: {setItem: (...v) => events.push(v)},
    location: {href: '/gui/security'},
};
context.window = context;
vm.createContext(context);
vm.runInContext(source, context);
const register = (name = ' My phone ') => context.registerSecurityPasskey('person@example.test', name);
const plain = value => JSON.parse(JSON.stringify(value));
const status = (result, expected) => {
    assert.equal(result.status, expected);
    assert.ok(Object.keys(result).every(k => ['status', 'message'].includes(k)));
    if (result.message !== undefined) assert.equal(typeof result.message, 'string');
    assert.ok(!JSON.stringify(result).includes('test-only-protocol-marker'));
};

(async () => {
    if (scenario === 'success') {
        status(await register(), 'success');
        assert.equal(calls.length, 2);
        assert.deepEqual(calls.map(c => c.url), [
            '/auth/webauthn/register/begin', '/auth/webauthn/register/complete']);
        for (const c of calls) {
            assert.equal(c.init.method, 'POST');
            assert.equal(c.init.credentials, 'same-origin');
            assert.equal(c.init.headers['Content-Type'], 'application/json');
        }
        assert.deepEqual(JSON.parse(calls[0].init.body), {username: 'person@example.test'});
        const publicKey = created[0].publicKey;
        assert.deepEqual(Array.from(publicKey.challenge), [251, 255, 0]);
        assert.deepEqual(Array.from(publicKey.user.id), [1, 2]);
        assert.deepEqual(Array.from(publicKey.excludeCredentials[0].id), [0, 255, 254]);
        assert.equal(publicKey.excludeCredentials[0].transports[0], 'usb');
        assert.deepEqual(JSON.parse(calls[1].init.body), {
            username: 'person@example.test', name: 'My phone',
            id: 'credential-id', rawId: '-_8A', type: 'public-key',
            response: {attestationObject: 'AP_-', clientDataJSON: 'e30', transports: ['internal', 'hybrid']},
        });
        status(await register(), 'success');
        assert.equal(calls.length, 4); // fresh begin, never reuse an attestation
    } else if (scenario === 'unicode') {
        status(await register('  ' + '🔑'.repeat(64) + '  '), 'success');
        assert.equal(JSON.parse(calls[1].init.body).name, '🔑'.repeat(64));
        status(await register('🔑'.repeat(65)), 'error');
        assert.equal(calls.length, 2);
    } else if (scenario === 'invalid_name') {
        for (const name of ['', ' \n\t ', null, 42, 'a'.repeat(65)]) {
            status(await register(name), 'error');
        }
        status(await context.registerSecurityPasskey('', 'phone'), 'error');
        assert.equal(calls.length, 0);
    } else if (scenario.startsWith('unsupported_')) {
        if (scenario === 'unsupported_insecure') context.isSecureContext = false;
        if (scenario === 'unsupported_credential') context.PublicKeyCredential = undefined;
        if (scenario === 'unsupported_api') context.navigator.credentials = undefined;
        if (scenario === 'unsupported_create') context.navigator.credentials.create = undefined;
        if (scenario === 'unsupported_abort') context.AbortController = undefined;
        status(await register(), 'error');
        assert.equal(calls.length, 0);
    } else if (scenario === 'cancel_prompt' || scenario === 'pending_lock') {
        const gate = deferred();
        doCreate = ({signal}) => {
            signal.addEventListener('abort', () => gate.resolve(null), {once: true});
            return gate.promise;
        };
        const pending = register();
        await tick();
        assert.equal(created.length, 1);
        assert.equal(created[0].signal.aborted, false);
        // Repeated loader execution must preserve the active lock/controller.
        vm.runInContext(source, context);
        status(await register(), 'error');
        assert.equal(calls.length, 1);
        if (scenario === 'cancel_prompt') {
            context.cancelSecurityPasskey();
            assert.equal(created[0].signal.aborted, true);
            status(await pending, 'cancelled');
            assert.equal(calls.length, 1);
        } else {
            gate.resolve(credential());
            status(await pending, 'success');
        }
        doCreate = async () => credential();
        context.cancelSecurityPasskey(); // no-op after finally
        status(await register(), 'success');
    } else if (scenario === 'cancel_begin') {
        const gate = deferred();
        doFetch = () => gate.promise;
        const pending = register();
        context.cancelSecurityPasskey();
        gate.resolve(response(options())); // even if fetch ignores abort
        status(await pending, 'cancelled');
        assert.equal(created.length, 0);
    } else if (scenario === 'cancel_during_complete') {
        const gate = deferred();
        doFetch = url => url.endsWith('/begin') ? response(options()) : gate.promise;
        const pending = register();
        await tick();
        assert.equal(calls.length, 2);
        context.cancelSecurityPasskey();
        status(await register(), 'error'); // cancellation cannot undo a server commit
        gate.resolve(response({access_token: 'test-only-protocol-marker'}));
        status(await pending, 'success');
    } else if (scenario === 'null_credential') {
        doCreate = async () => null;
        status(await register(), 'cancelled');
        assert.equal(calls.length, 1);
    } else if (scenario === 'user_cancel' || scenario === 'abort_error' || scenario === 'device_error') {
        doCreate = async () => { throw new DOMException('not displayed',
            scenario === 'user_cancel' ? 'NotAllowedError' : scenario === 'abort_error' ? 'AbortError' : 'InvalidStateError'); };
        status(await register(), scenario === 'device_error' ? 'error' : 'cancelled');
        assert.equal(calls.length, 1);
        doCreate = async () => credential();
        status(await register(), 'success');
    } else if (scenario === 'begin_bad_status' || scenario === 'begin_nonjson' || scenario === 'begin_network') {
        doFetch = async () => {
            if (scenario === 'begin_network') throw new TypeError('network lost');
            return scenario === 'begin_nonjson' ? nonJSON(200) : response({detail: 'not displayed'}, 401);
        };
        status(await register(), 'error');
        assert.equal(created.length, 0);
        assert.equal(calls.length, 1);
    } else if (scenario.startsWith('complete_')) {
        doFetch = async url => {
            if (url.endsWith('/begin')) return response(options());
            if (scenario === 'complete_network') throw new TypeError('network lost');
            if (scenario === 'complete_nonjson') return nonJSON(200);
            if (scenario === 'complete_nonjson_error') return nonJSON(502);
            if (scenario === 'complete_missing_token') return response({});
            return response({access_token: 'test-only-protocol-marker'}, 400);
        };
        const result = await register();
        const uncertain = ['complete_network', 'complete_nonjson', 'complete_missing_token'].includes(scenario);
        status(result, uncertain ? 'uncertain' : 'error');
        if (uncertain) assert.match(result.message, /check|refresh/i);
        assert.equal(calls.length, 2); // no automatic retry
        doFetch = async url => url.endsWith('/begin') ? response(options()) : response({access_token: 'test-only-protocol-marker'});
        status(await register(), 'success'); // finally released lock
    } else if (scenario === 'optional_transports_and_large_buffer') {
        doCreate = async () => {
            const c = credential();
            delete c.response.getTransports;
            c.response.attestationObject = new Uint8Array(200000).buffer;
            return c;
        };
        status(await register(), 'success');
        const payload = JSON.parse(calls[1].init.body);
        assert.deepEqual(payload.response.transports, []);
        assert.equal(Buffer.from(payload.response.attestationObject, 'base64url').length, 200000);
    } else {
        throw new Error('Unknown scenario');
    }
    assert.deepEqual(events, []); // neither logging nor browser storage writes
    assert.equal(context.location.href, '/gui/security');
    process.stdout.write('PASS ' + scenario);
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
"""


@pytest.mark.parametrize(
    "scenario",
    [
        "success", "unicode", "invalid_name",
        "unsupported_insecure", "unsupported_credential", "unsupported_api",
        "unsupported_create", "unsupported_abort",
        "cancel_prompt", "pending_lock", "cancel_begin", "cancel_during_complete",
        "null_credential", "user_cancel", "abort_error", "device_error",
        "begin_bad_status", "begin_nonjson", "begin_network",
        "complete_bad_status", "complete_nonjson", "complete_nonjson_error",
        "complete_missing_token", "complete_network", "optional_transports_and_large_buffer",
    ],
)
def test_browser_contract(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required to exercise the actual production JavaScript"
    result = subprocess.run(
        [node, "-e", HARNESS, scenario, str(SCRIPT)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"PASS {scenario}"


def test_loader_adds_static_javascript_without_user_interpolation():
    from beaverhabits.frontend.security_passkeys import add_security_passkeys_javascript

    with patch("beaverhabits.frontend.security_passkeys.ui.add_head_html") as add:
        add_security_passkeys_javascript()
    add.assert_called_once()
    html = add.call_args.args[0]
    assert html == f"<script>{SCRIPT.read_text(encoding='utf-8')}</script>"
    assert "</script" not in SCRIPT.read_text(encoding="utf-8").lower()
