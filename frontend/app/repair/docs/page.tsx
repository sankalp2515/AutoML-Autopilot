import { Suspense } from "react";
import Docs from "@/components/repair/Docs";
import { Loading } from "@/components/repair/ui";
export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <Docs />
    </Suspense>
  );
}
