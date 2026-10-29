import { Suspense } from "react";
import NewRepair from "@/components/repair/NewRepair";
import { Loading } from "@/components/repair/ui";
export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <NewRepair />
    </Suspense>
  );
}
