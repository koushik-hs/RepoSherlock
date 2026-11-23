type IconName = "brand" | "folder" | "findings" | "activity" | "search" | "lock" | "code";

const marks = {
  brand: <><circle cx="11" cy="11" r="5"/><path d="m15 15 4 4M12 3v3M3 12h3"/></>,
  folder: <path d="M3 7.5h7l2 2H21v9H3zM3 7.5V5h7l2 2h7v2"/>,
  findings: <><path d="M7 3h7l4 4v14H6V3h1Zm6 0v5h5M9 12h6m-6 4h6"/><path d="m8 8 1 1 2-2"/></>,
  activity: <path d="M3 12h4l3-6 4 12 3-6h4"/>,
  search: <><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/></>,
  lock: <path d="M6 11h12v9H6zM9 11V7a3 3 0 0 1 6 0v4m-3 4v2"/>,
  code: <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 14"/>,
};

export function Icon({ name }: { name: IconName }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24" fill="none"
    stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">{marks[name]}</svg>;
}
