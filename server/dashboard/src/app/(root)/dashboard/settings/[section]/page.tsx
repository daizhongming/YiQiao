import { redirect } from "next/navigation";

const LEGACY_SECTION_MAP: Record<string, string> = {
  projects: "project-general",
  members: "org-members",
  extraction: "project-extraction",
  "categories-retention": "project-categories",
  playground: "project-playground",
  profile: "profile",
  password: "profile",
};

export default async function SettingsSectionPage({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  redirect(
    `/dashboard/settings?tab=${LEGACY_SECTION_MAP[section] || "project-general"}`,
  );
}
