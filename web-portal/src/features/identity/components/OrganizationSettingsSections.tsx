/**
 * The organization settings sections (ACC-23, ID-45): one card per setting
 * group, each with its own Save. `organization_settings` is tracked, so a save
 * opens the reason dialog and sends the reason with the change.
 */
import type { Schema } from "@g3/api-client";
import { App, Button, Card, Flex, Form, InputNumber } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryState } from "../../../shared/ui/QueryState";
import { ReasonDialog } from "../../../shared/ui/ReasonDialog";
import { useReportApiError } from "../../../shared/ui/feedback";
import { useOrganizationSettingsQuery } from "../api/queries";
import { useUpdateOrganizationSettingsMutation } from "../api/mutations";

/** The organization's settings as the API returns them. */
type OrganizationSettings = Schema<"OrganizationSettingsResponse">;

/** A setting the sections can edit (a numeric field of the update request). */
type SettingField =
  "driving_session_auto_end_minutes" | "telemetry_interval_seconds";

/**
 * Props of `SettingSectionCard`.
 *
 * Attributes:
 *   organizationId: Whose settings.
 *   settings: The saved values.
 *   field: The setting this card edits.
 *   title: The card title.
 *   label: The field label.
 *   help: The allowed range, in words.
 *   min, max: The allowed range (the request schema's limits).
 */
interface SettingSectionCardProps {
  organizationId: string;
  settings: OrganizationSettings;
  field: SettingField;
  title: string;
  label: string;
  help: string;
  min: number;
  max: number;
}

/** One settings card: a number field, Save, and the reason dialog. */
function SettingSectionCard({
  organizationId,
  settings,
  field,
  title,
  label,
  help,
  min,
  max,
}: SettingSectionCardProps) {
  const { t } = useTranslation("identity");
  const { t: tCommon } = useTranslation();
  const { message } = App.useApp();
  // A cleared number field holds `null`; the required rule stops it at Save.
  const [form] = Form.useForm<Partial<Record<SettingField, number | null>>>();
  const [isReasonDialogOpen, setIsReasonDialogOpen] = useState(false);
  const updateSettingsMutation =
    useUpdateOrganizationSettingsMutation(organizationId);
  const reportApiError = useReportApiError();
  const value = Form.useWatch(field, form);
  const isChanged =
    value !== undefined && value !== null && value !== settings[field];

  return (
    <Card title={title}>
      <Form
        form={form}
        layout="vertical"
        initialValues={{ [field]: settings[field] }}
        onFinish={() => {
          setIsReasonDialogOpen(true);
        }}
      >
        <Form.Item
          name={field}
          label={label}
          extra={help}
          rules={[{ required: true, message: tCommon("fieldErrors.missing") }]}
        >
          <InputNumber min={min} max={max} precision={0} />
        </Form.Item>
        <Flex justify="end">
          <Button type="primary" htmlType="submit" disabled={!isChanged}>
            {tCommon("actions.save")}
          </Button>
        </Flex>
      </Form>
      <ReasonDialog
        open={isReasonDialogOpen}
        title={t("settings.reasonTitle")}
        description={t("settings.reasonDescription")}
        confirmText={tCommon("actions.save")}
        onCancel={() => {
          setIsReasonDialogOpen(false);
        }}
        onConfirm={async (reason) => {
          try {
            await updateSettingsMutation.mutateAsync({
              [field]: form.getFieldsValue()[field],
              reason,
            });
            setIsReasonDialogOpen(false);
            void message.success(t("settings.saveSuccess"));
          } catch (error) {
            setIsReasonDialogOpen(false);
            reportApiError(error, form);
          }
        }}
      />
    </Card>
  );
}

/**
 * Props of `OrganizationSettingsSections`.
 *
 * Attributes:
 *   organizationId: The organization whose settings are shown (the current one).
 */
interface OrganizationSettingsSectionsProps {
  organizationId: string;
}

/** Driving-session and vehicle-data settings of an organization. */
export function OrganizationSettingsSections({
  organizationId,
}: OrganizationSettingsSectionsProps) {
  const { t } = useTranslation("identity");
  const settingsQuery = useOrganizationSettingsQuery(organizationId);

  return (
    <QueryState query={settingsQuery}>
      {(settings) => (
        <>
          <SettingSectionCard
            organizationId={organizationId}
            settings={settings}
            field="driving_session_auto_end_minutes"
            title={t("settings.drivingSessionsTitle")}
            label={t("settings.autoEndMinutes")}
            help={t("settings.autoEndHelp")}
            min={5}
            max={1440}
          />
          <SettingSectionCard
            organizationId={organizationId}
            settings={settings}
            field="telemetry_interval_seconds"
            title={t("settings.telemetryTitle")}
            label={t("settings.telemetryIntervalSeconds")}
            help={t("settings.telemetryIntervalHelp")}
            min={5}
            max={3600}
          />
        </>
      )}
    </QueryState>
  );
}
