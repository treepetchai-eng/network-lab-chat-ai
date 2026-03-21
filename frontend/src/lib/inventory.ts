export interface InventoryDevice {
  hostname: string;
  ipAddress: string;
  role: string;
  platform: string;
  location: string;
  version?: string;
}

interface InventoryApiShape {
  hostname: string;
  ip_address: string;
  os_platform: string;
  device_role: string;
  site: string;
  version: string;
}

export function normalizeInventoryDevice(device: InventoryApiShape): InventoryDevice {
  return {
    hostname: device.hostname,
    ipAddress: device.ip_address,
    platform: device.os_platform,
    role: device.device_role,
    location: device.site,
    version: device.version,
  };
}

export const LAB_INVENTORY_SNAPSHOT: InventoryDevice[] = [
  { hostname: "LAB-MGMT-BR01", ipAddress: "10.255.0.1", platform: "cisco_ios", role: "router", location: "MGMT", version: "15.6(2)T" },
  { hostname: "HQ-CORE-RT01", ipAddress: "10.255.1.11", platform: "cisco_ios", role: "core_router", location: "HQ", version: "15.6(2)T" },
  { hostname: "HQ-CORE-RT02", ipAddress: "10.255.1.12", platform: "cisco_ios", role: "core_router", location: "HQ", version: "15.6(2)T" },
  { hostname: "HQ-DIST-GW01", ipAddress: "10.255.2.21", platform: "cisco_ios", role: "dist_switch", location: "HQ", version: "15.6(2)T" },
  { hostname: "HQ-DIST-GW02", ipAddress: "10.255.2.22", platform: "cisco_ios", role: "dist_switch", location: "HQ", version: "15.6(2)T" },
  { hostname: "BRANCH-A-RTR", ipAddress: "10.255.3.101", platform: "cisco_ios", role: "router", location: "BRANCH-A", version: "15.6(2)T" },
  { hostname: "BRANCH-B-RTR", ipAddress: "10.255.3.102", platform: "cisco_ios", role: "router", location: "BRANCH-B", version: "15.6(2)T" },
  { hostname: "BRANCH-A-Switch", ipAddress: "192.168.99.11", platform: "cisco_ios", role: "access_switch", location: "BRANCH-A", version: "15.2(4.0.55)E" },
  { hostname: "BRANCH-B-Switch", ipAddress: "192.168.199.11", platform: "cisco_ios", role: "access_switch", location: "BRANCH-B", version: "15.2(4.0.55)E" },
];

export const LAB_INVENTORY = LAB_INVENTORY_SNAPSHOT;
