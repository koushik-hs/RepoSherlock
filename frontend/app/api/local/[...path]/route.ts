import { localBridge } from "../../../../lib/local-bridge";

export const runtime = "nodejs";

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return localBridge(request, path, {
    bearer: process.env.REPOSHERLOCK_SESSION_TOKEN,
    pairing: process.env.REPOSHERLOCK_BROWSER_PAIRING_CODE,
    origin: process.env.REPOSHERLOCK_API_ORIGIN,
  });
}

export { handle as GET, handle as POST };
