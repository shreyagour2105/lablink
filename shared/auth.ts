import roles from "./roles.json";

export type Role = keyof typeof roles;
export type SessionResponse = { role: Role };

export function isSessionResponse(value: unknown): value is SessionResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "role" in value &&
    typeof value.role === "string" &&
    Object.hasOwn(roles, value.role)
  );
}
