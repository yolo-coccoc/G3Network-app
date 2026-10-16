/**
 * Public surface of the identity feature (web-portal-conventions.md §4): only
 * re-exports, no logic. Other features and routes import from here only.
 */
export {
  useLoginMutation,
  useSwitchOrganizationMutation,
} from "./api/mutations";
export { LoginForm } from "./components/LoginForm";
export { OrganizationName } from "./components/OrganizationName";
export { OrganizationPicker } from "./components/OrganizationPicker";
export { OrganizationSelect } from "./components/OrganizationSelect";
export { OrganizationSettingsSections } from "./components/OrganizationSettingsSections";
export { OrganizationSwitcher } from "./components/OrganizationSwitcher";
export { ORGANIZATION_SETTINGS_FEATURE } from "./permissions";
