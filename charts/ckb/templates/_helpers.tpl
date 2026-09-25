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
