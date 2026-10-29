"use client";
import { Action, Button, Notice, PageHead } from "@/components/repair/ui";
export default function ErrorPage({
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <>
      <PageHead title="This view could not be opened" />
      <Notice
        title="The interface stopped before completing this action"
        tone="error"
        action={
          <>
            <Button onClick={reset}>Try again</Button>
            <Action href="/repair">Return home</Action>
          </>
        }
      >
        No successful repair is implied. Retry the view or return to your run
        history to inspect the saved state.
      </Notice>
    </>
  );
}
