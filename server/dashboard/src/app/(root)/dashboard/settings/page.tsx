// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import SettingsCloudClient, {
  type CloudSettingsSection,
} from "./settings-cloud-client";

const SECTIONS = new Set<CloudSettingsSection>([
  "project-general",
  "project-extraction",
  "project-categories",
  "project-retention",
  "project-playground",
  "org-general",
  "org-members",
  "profile",
]);

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab } = await searchParams;
  const section = SECTIONS.has(tab as CloudSettingsSection)
    ? (tab as CloudSettingsSection)
    : "project-general";
  return <SettingsCloudClient section={section} />;
}
