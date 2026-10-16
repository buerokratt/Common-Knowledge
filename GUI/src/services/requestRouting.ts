export const isAccountRequest = (path: string): boolean => /^(accounts|auth)\//.test(path.replace(/^\//, ''));

export const rolesFromResponse = (data: any): string[] => {
  const roles = data?.response ?? data?.data?.get_user?.[0]?.authorities ?? data;
  return Array.isArray(roles) ? roles.filter((role): role is string => typeof role === 'string') : [];
};
