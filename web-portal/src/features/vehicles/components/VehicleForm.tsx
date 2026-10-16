/**
 * The vehicle form (VEH-01): register a vehicle, or edit one.
 *
 * Create: plate, VIN, model, year, handover date, and the owner for internal
 * staff (customers always register for their own organization, ID-50).
 * Edit: the same identity fields plus the service status. `vehicles` is
 * tracked; its update request has no `reason` field: a typed `status_reason`
 * becomes the history reason, otherwise the backend writes a fixed one
 * (ID-50 (5)). So a status change requires its reason here, and a plain edit
 * opens no reason dialog.
 */
import type { Schema } from "@g3/api-client";
import {
  App,
  Button,
  DatePicker,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
} from "antd";
import { useTranslation } from "react-i18next";

import { useSessionStore } from "../../../shared/auth/sessionStore";
import { vietnamDateToUtcIso } from "../../../shared/lib/vietnamTime";
import { useReportApiError } from "../../../shared/ui/feedback";
import { OrganizationSelect } from "../../identity";
import {
  useCreateVehicleMutation,
  useUpdateVehicleMutation,
} from "../api/mutations";
import { useVehicleModelListQuery } from "../api/queries";
import { VEHICLE_STATUSES, type VehicleStatus } from "../search";

/** A vehicle as the API returns it. */
type Vehicle = Schema<"VehicleResponse">;

/** The date picker's value; only its calendar date is used. */
interface PickedDate {
  format: (template: string) => string;
}

/**
 * The form's fields (API names, so a 422 lands on the right field).
 *
 * Attributes:
 *   acquired_at: Handover date, create only; empty means today (backend default).
 *   organization_id: Owner, create only, internal staff only.
 *   status, status_reason: Edit only.
 */
interface VehicleFormValues {
  license_plate: string;
  vin: string;
  vehicle_model_id: string;
  year: number;
  acquired_at?: PickedDate | null;
  organization_id?: string;
  status?: VehicleStatus;
  status_reason?: string;
}

/** Earliest and latest model year the API accepts. */
const YEAR_RANGE = { min: 1900, max: 2100 };

/**
 * Props of `VehicleForm`.
 *
 * Attributes:
 *   vehicle: The vehicle to edit; omitted to register a new one.
 *   onSaved: Called with the saved vehicle.
 *   onCancel: Leave without saving.
 */
interface VehicleFormProps {
  vehicle?: Vehicle;
  onSaved: (vehicle: Vehicle) => void;
  onCancel: () => void;
}

/**
 * Build the PATCH body: only the fields that changed (conventions: partial
 * updates send what changes; `null` is never used to clear a field).
 */
function toVehicleUpdateRequest(
  vehicle: Vehicle,
  values: VehicleFormValues,
): Schema<"VehicleUpdateRequest"> {
  const vehicleUpdateRequest: Schema<"VehicleUpdateRequest"> = {};
  if (values.license_plate !== vehicle.license_plate) {
    vehicleUpdateRequest.license_plate = values.license_plate;
  }
  if (values.vin !== vehicle.vin) {
    vehicleUpdateRequest.vin = values.vin;
  }
  if (values.vehicle_model_id !== vehicle.vehicle_model_id) {
    vehicleUpdateRequest.vehicle_model_id = values.vehicle_model_id;
  }
  if (values.year !== vehicle.year) {
    vehicleUpdateRequest.year = values.year;
  }
  if (values.status !== undefined && values.status !== vehicle.status) {
    vehicleUpdateRequest.status = values.status;
    vehicleUpdateRequest.status_reason = values.status_reason?.trim();
  }
  return vehicleUpdateRequest;
}

/** Register or edit a vehicle. */
export function VehicleForm({ vehicle, onSaved, onCancel }: VehicleFormProps) {
  const { t } = useTranslation("vehicles");
  const { t: tCommon } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<VehicleFormValues>();
  const isInternal = useSessionStore((state) => state.me?.is_internal ?? false);
  const vehicleModelListQuery = useVehicleModelListQuery();
  const createVehicleMutation = useCreateVehicleMutation();
  const updateVehicleMutation = useUpdateVehicleMutation(
    vehicle?.vehicle_id ?? "",
  );
  const reportApiError = useReportApiError();
  const isEdit = vehicle !== undefined;
  const chosenStatus = Form.useWatch("status", form);
  const isStatusChanged =
    isEdit && chosenStatus !== undefined && chosenStatus !== vehicle.status;
  const requiredRule = {
    required: true,
    message: tCommon("fieldErrors.missing"),
  };

  const save = (values: VehicleFormValues) => {
    const onError = (error: unknown) => {
      reportApiError(error, form);
    };
    if (isEdit) {
      updateVehicleMutation.mutate(toVehicleUpdateRequest(vehicle, values), {
        onSuccess: (savedVehicle) => {
          void message.success(t("form.updateSuccess"));
          onSaved(savedVehicle);
        },
        onError,
      });
      return;
    }
    const pickedDate = values.acquired_at?.format("YYYY-MM-DD");
    createVehicleMutation.mutate(
      {
        license_plate: values.license_plate.trim(),
        vin: values.vin.trim(),
        vehicle_model_id: values.vehicle_model_id,
        year: values.year,
        status: "ACTIVE",
        acquired_at:
          pickedDate === undefined
            ? undefined
            : vietnamDateToUtcIso(pickedDate),
        organization_id: values.organization_id,
      },
      {
        onSuccess: (savedVehicle) => {
          void message.success(t("form.createSuccess"));
          onSaved(savedVehicle);
        },
        onError,
      },
    );
  };

  return (
    <Form<VehicleFormValues>
      form={form}
      layout="vertical"
      initialValues={
        vehicle && {
          license_plate: vehicle.license_plate,
          vin: vehicle.vin,
          vehicle_model_id: vehicle.vehicle_model_id,
          year: vehicle.year,
          status: vehicle.status,
          status_reason: vehicle.status_reason ?? undefined,
        }
      }
      onFinish={save}
    >
      <Form.Item
        name="license_plate"
        label={t("fields.licensePlate")}
        rules={[requiredRule]}
      >
        <Input maxLength={20} />
      </Form.Item>
      <Form.Item
        name="vin"
        label={t("fields.vin")}
        extra={t("form.vinHelp")}
        normalize={(value: string) => value.toUpperCase()}
        rules={[requiredRule, { len: 17, message: t("form.vinHelp") }]}
      >
        <Input maxLength={17} />
      </Form.Item>
      <Form.Item
        name="vehicle_model_id"
        label={t("fields.model")}
        rules={[requiredRule]}
      >
        <Select
          showSearch={{ optionFilterProp: "label" }}
          loading={vehicleModelListQuery.isPending}
          placeholder={t("form.modelPlaceholder")}
          options={vehicleModelListQuery.data?.items.map((vehicleModel) => ({
            value: vehicleModel.vehicle_model_id,
            label: `${vehicleModel.make} ${vehicleModel.model_name}`,
          }))}
        />
      </Form.Item>
      <Form.Item name="year" label={t("fields.year")} rules={[requiredRule]}>
        <InputNumber min={YEAR_RANGE.min} max={YEAR_RANGE.max} precision={0} />
      </Form.Item>
      {!isEdit && (
        <Form.Item
          name="acquired_at"
          label={t("fields.acquiredAt")}
          extra={t("form.acquiredAtHelp")}
        >
          <DatePicker
            format="DD/MM/YYYY"
            placeholder={t("form.acquiredAtPlaceholder")}
          />
        </Form.Item>
      )}
      {!isEdit && isInternal && (
        <Form.Item
          name="organization_id"
          label={t("fields.owner")}
          extra={t("form.ownerHelp")}
        >
          <OrganizationSelect />
        </Form.Item>
      )}
      {isEdit && (
        <Form.Item
          name="status"
          label={t("fields.status")}
          rules={[requiredRule]}
        >
          <Select<VehicleStatus>
            options={VEHICLE_STATUSES.map((status) => ({
              value: status,
              label: tCommon(`status.${status}`),
            }))}
          />
        </Form.Item>
      )}
      {isStatusChanged && (
        <Form.Item
          name="status_reason"
          label={t("fields.statusReason")}
          extra={t("form.statusReasonHelp")}
          rules={[
            {
              required: true,
              whitespace: true,
              message: t("form.statusReasonRequired"),
            },
          ]}
        >
          <Input.TextArea rows={2} maxLength={200} showCount />
        </Form.Item>
      )}
      <Flex justify="end" gap="small">
        <Button onClick={onCancel}>{tCommon("actions.cancel")}</Button>
        <Button
          type="primary"
          htmlType="submit"
          loading={
            createVehicleMutation.isPending || updateVehicleMutation.isPending
          }
        >
          {tCommon("actions.save")}
        </Button>
      </Flex>
    </Form>
  );
}
