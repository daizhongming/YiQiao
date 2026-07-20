// This file was modified in 2026 by YiQiao contributors. See NOTICE.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const isValidEmail = (value: string) => EMAIL_RE.test(value.trim());

const MAX_CATEGORIES = 100;

export const getCategoryValidationError = (
  categories: readonly { name: string }[],
) => {
  if (categories.some((category) => !category.name.trim())) {
    return "Each category requires a name.";
  }
  if (categories.length > MAX_CATEGORIES) {
    return "A project can have at most 100 categories.";
  }
  return null;
};
