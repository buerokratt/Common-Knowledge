{{/*
Rule 1, chart side. Returns contentExternal.workDir, or aborts the render.

Mirrors assert_not_under_scrapped_data() in
content-external/exporter/api/config.py. Rule 1 is asserted in three places
because one is easy to bypass: docker-compose.yml (A5), here (A8), and the
service itself (A10).

This is a helper that RETURNS the value rather than a standalone guard block,
because the deployment cannot use workDir without going through it — a guard
block can be deleted and everything still renders.

Component-wise, not substring: /var/lib/scrapped-data-archive is a different
directory and is allowed; /uploads/scrapped-data/x is not.
*/}}
{{- define "ckb.contentExternal.workDir" -}}
{{- $wd := .Values.contentExternal.workDir | default "" -}}
{{- if not $wd -}}
{{- fail "contentExternal.workDir must be set — it holds the run lock and the deletion journal" -}}
{{- end -}}
{{- if not (hasPrefix "/" $wd) -}}
{{- fail (printf "contentExternal.workDir must be an absolute path, got %q" $wd) -}}
{{- end -}}
{{- $cleaned := clean $wd -}}
{{- if contains ".." $cleaned -}}
{{- fail (printf "contentExternal.workDir must be normalised with no '..' segments, got %q" $wd) -}}
{{- end -}}
{{- range $part := splitList "/" (lower $cleaned) -}}
{{- if eq $part "scrapped-data" -}}
{{- fail (printf "rule 1: contentExternal.workDir (%q) is inside a 'scrapped-data' tree. Everything written there is zipped into the Global Classifier's payload on the next hourly run, silently. Point it at this service's own PVC." $wd) -}}
{{- end -}}
{{- end -}}
{{- $cleaned -}}
{{- end -}}

{{/*
A14, chart side. Returns contentExternal.persistence.accessMode, or aborts.

The third of A14's three deployment facts, and the one that had no mechanism.
`replicas: 1` and `strategy: Recreate` are asserted by being written in the
deployment template, where deleting them is a visible act. The access mode was
only a comment, and a comment does not survive someone switching to an RWX
storage class to make a rollout work.

Why RWX is refused rather than warned about. F2's flock is the entire defence
against an export and a drain overlapping (flagged risk 8), and flock is sound
only within one kernel. On NFS/EFS/CephFS it is advisory and routinely not
honoured across nodes, so two pods both "acquire" it and run concurrent
exports WITH NO ERROR. On RWO the second pod simply cannot mount, which is a
broken deploy rather than corrupted state — so RWO is the safe failure and RWX
is the silent one.

Same shape as ckb.contentExternal.workDir: a helper that RETURNS the value, so
the PVC cannot be rendered without going through the guard. A standalone
guard block can be deleted and everything still renders.
*/}}
{{- define "ckb.contentExternal.accessMode" -}}
{{- $mode := .Values.contentExternal.persistence.accessMode | default "" -}}
{{- if not $mode -}}
{{- fail "contentExternal.persistence.accessMode must be set — it must be ReadWriteOnce, because F2's run lock depends on flock, which is only sound within one kernel" -}}
{{- end -}}
{{- if ne $mode "ReadWriteOnce" -}}
{{- fail (printf "A14: contentExternal.persistence.accessMode must be ReadWriteOnce, got %q. The run lock is an flock and is only sound within one kernel. On a ReadWriteMany volume (NFS/EFS/CephFS) flock is advisory and routinely not honoured across nodes, so an export and a drain would run concurrently with no error at all — a drain deleting a key an export just re-published. Use a block or local-backed StorageClass." $mode) -}}
{{- end -}}
{{- $mode -}}
{{- end -}}

{{/*
L11, chart side. Returns contentExternal.vaultAgent.auth.method, or aborts.

The built-in Vault Agent sidecar (contentExternal.vaultAgent) keeps the token
the llm-module sink reads on every operation fresh, as compose's
vault-agent-content does. Same shape as the two helpers above: the sidecar's
config cannot be rendered without going through this.

What it refuses, and why:
- an unknown method;
- `kubernetes` without a named service account. The Vault role binds to a
  service account; binding it to `default` hands the credential to every pod
  in the namespace that runs as `default`;
- `approle` without the Secret holding role_id and secret_id;
- VAULT_ADDR or VAULT_TOKEN_PATH also set in contentExternal.env. The sidecar
  sets both, from the same values it mounts the token with; a second value
  would win or lose by list order, and a token path that misses the mount is
  `credential unavailable` on every push;
- the Vault Agent Injector's annotation as well. Two agents would each write
  a token, to different places.
*/}}
{{- define "ckb.contentExternal.vaultAgentMethod" -}}
{{- $va := .Values.contentExternal.vaultAgent -}}
{{- $method := $va.auth.method | default "" -}}
{{- if not (has $method (list "kubernetes" "approle")) -}}
{{- fail (printf "contentExternal.vaultAgent.auth.method must be kubernetes or approle, got %q" $method) -}}
{{- end -}}
{{- if and (eq $method "kubernetes") (not (include "ckb.contentExternal.serviceAccountName" .)) -}}
{{- fail "contentExternal.vaultAgent.auth.method=kubernetes needs a named service account: set contentExternal.serviceAccount.create=true or contentExternal.serviceAccount.name. The Vault role binds to it, and binding it to `default` would hand the llm-module credential to every pod in the namespace that runs as `default`." -}}
{{- end -}}
{{- if and (eq $method "approle") (not $va.auth.approleSecret.name) -}}
{{- fail "contentExternal.vaultAgent.auth.method=approle needs contentExternal.vaultAgent.auth.approleSecret.name: a Secret holding the role_id and secret_id. Note that vault/init.sh gives secret_id a 24h TTL, so something must rotate that Secret — kubernetes auth needs no rotation." -}}
{{- end -}}
{{- range .Values.contentExternal.env -}}
{{- if has .name (list "VAULT_ADDR" "VAULT_TOKEN_PATH") -}}
{{- fail (printf "contentExternal.env sets %s, but contentExternal.vaultAgent is enabled and sets it itself (vaultAgent.vaultAddr / vaultAgent.tokenMountPath). Remove it from env." .name) -}}
{{- end -}}
{{- end -}}
{{- if hasKey (.Values.contentExternal.podAnnotations | default dict) "vault.hashicorp.com/agent-inject" -}}
{{- fail "contentExternal.vaultAgent is enabled AND podAnnotations asks the Vault Agent Injector for an agent. Use one: two agents write two tokens, and the service reads only one of them." -}}
{{- end -}}
{{- $method -}}
{{- end -}}

{{/*
The service account content-external runs as: the created one, the named one,
or empty (the namespace default, rendered as no serviceAccountName at all).
*/}}
{{- define "ckb.contentExternal.serviceAccountName" -}}
{{- $sa := .Values.contentExternal.serviceAccount | default dict -}}
{{- if $sa.create -}}
{{- $sa.name | default .Values.contentExternal.nameOverride -}}
{{- else -}}
{{- $sa.name | default "" -}}
{{- end -}}
{{- end -}}
