import {
  ClipboardCheck,
  GitBranch,
  History,
  LayoutDashboard,
  Network,
  Siren,
  type LucideIcon,
} from "lucide-react";

export interface AppNavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  description: string;
  section: "start";
}

export const APP_NAV_ITEMS: AppNavItem[] = [
  { href: "/ops", label: "Overview", icon: LayoutDashboard, description: "Open incidents and recent reports.", section: "start" },
  { href: "/ops/incidents", label: "Incidents", icon: Siren, description: "Open and active incidents.", section: "start" },
  { href: "/ops/history", label: "History", icon: History, description: "Resolved incident archive.", section: "start" },
  { href: "/ops/devices", label: "Devices", icon: Network, description: "Inventory and device health.", section: "start" },
  { href: "/ops/clusters", label: "Clusters", icon: GitBranch, description: "Correlated incident clusters.", section: "start" },
  { href: "/ops/approvals", label: "Approvals", icon: ClipboardCheck, description: "Fixes waiting for approval.", section: "start" },
];

export function isNavItemActive(pathname: string, href: string): boolean {
  if (href === "/" || href === "/ops") {
    return pathname === href;
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
