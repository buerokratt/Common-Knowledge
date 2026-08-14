// Redacts usernames/credentials from a raw scraper error message before it
// is shown in the UI (both in the table cell and its hover tooltip), while
// keeping the rest of the message intact.
export function getSanitizedErrorDetail(rawMessage?: string | null): string {
  if (!rawMessage) return '-';

  let sanitized = rawMessage;

  // Credentials embedded as URL userinfo, e.g. http://user:password@host.
  sanitized = sanitized.replace(
    /:\/\/[^\s/@]+:[^\s/@]+@/gi,
    '://[redacted]@'
  );

  // Basic auth headers, e.g. Authorization: Basic dXNlcjpwYXNz.
  sanitized = sanitized.replace(
    /\b(authorization\s*:\s*(basic|bearer)\s+)\S+/gi,
    '$1[redacted]'
  );

  // Common credential-style query params, e.g. ?token=..., &password=....
  sanitized = sanitized.replace(
    /([?&](?:token|api[_-]?key|password|passwd|secret|access[_-]?token|auth)=)[^&\s"'<>]+/gi,
    '$1[redacted]'
  );

  return sanitized;
}
