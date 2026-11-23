import { RepoSherlock } from "./reposherlock";
import { loopbackOrigin } from "../lib/local-bridge";

export const dynamic = "force-dynamic";

export default function Page() {
  let documentationOrigin: string | null = null;
  try {
    documentationOrigin = loopbackOrigin(process.env.REPOSHERLOCK_API_ORIGIN);
  } catch {
    // An invalid setting leaves the documentation links unavailable.
  }
  return <RepoSherlock documentationOrigin={documentationOrigin} />;
}
