import i18next, { TFunction, TOptions } from 'i18next';

/**
 * Translated message for a failed API call.
 *
 * Ruuter error responses carry a stable code ({ response: { code, error } }).
 * A code with a translation under `errors.<code>` is shown in the user's
 * language; anything else (no code, unknown code, network error) falls back to
 * the action's own message. The raw backend or axios text is never shown, only
 * logged for debugging.
 */
export const errorMessage = (
  error: any,
  t: TFunction,
  fallbackKey: string,
  fallbackOptions?: TOptions
): string => {
  console.error(error);
  const code = error?.response?.data?.response?.code;
  if (typeof code === 'string' && i18next.exists(`errors.${code}`)) {
    return t(`errors.${code}`) ?? '';
  }
  return String(t(fallbackKey, fallbackOptions ?? {}) ?? '');
};
