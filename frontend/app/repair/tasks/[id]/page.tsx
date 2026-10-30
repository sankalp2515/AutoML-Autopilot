import Workspace from "@/components/repair/Workspace";
export default function Page({ params }: { params: { id: string } }) {
  return <Workspace taskId={params.id} />;
}
