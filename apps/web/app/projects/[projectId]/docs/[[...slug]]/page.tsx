import { DocumentationReader } from "@/components/documentation-reader";

export default async function DocumentationPage({
  params,
}: {
  params: Promise<{ projectId: string; slug?: string[] }>;
}) {
  const { projectId, slug } = await params;
  return <DocumentationReader projectId={projectId} initialSlug={(slug || []).join("/")} />;
}
