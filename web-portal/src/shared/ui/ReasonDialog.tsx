/**
 * The reason dialog (web-portal-ui.md §4): every destructive or audited action
 * (soft delete, decided status change, update of a tracked table, export,
 * charger command...) goes through it. It states what will happen and asks
 * for a reason: required, 1 to 200 characters, as the backend's `Reason`
 * type. Destructive actions get a danger button. No `window.confirm`.
 */
import { Form, Input, Modal, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

/** The backend's `Reason` limit (`libs/common/reason.py`). */
const REASON_MAX_LENGTH = 200;

/**
 * Props of `ReasonDialog`.
 *
 * Attributes:
 *   open: Whether the dialog is shown.
 *   title: The action ("Remove the vehicle").
 *   description: What will happen.
 *   confirmText: The confirm button's label (a verb).
 *   isDanger: Red confirm button, for destructive actions.
 *   onConfirm: Runs the action with the trimmed reason; the dialog stays open
 *     (and shows its loading state) until the promise settles; on failure the
 *     caller reports the error and the dialog stays open.
 *   onCancel: Close without acting.
 */
interface ReasonDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmText: string;
  isDanger?: boolean;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
}

/** Shape of the dialog's form. */
interface ReasonFormValues {
  reason: string;
}

/** A confirmation that collects the reason of an audited action. */
export function ReasonDialog({
  open,
  title,
  description,
  confirmText,
  isDanger = false,
  onConfirm,
  onCancel,
}: ReasonDialogProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm<ReasonFormValues>();
  const [isSubmitting, setIsSubmitting] = useState(false);

  const confirm = async () => {
    const { reason } = await form.validateFields();
    setIsSubmitting(true);
    try {
      await onConfirm(reason.trim());
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={title}
      okText={confirmText}
      cancelText={t("actions.cancel")}
      okButtonProps={{ danger: isDanger }}
      confirmLoading={isSubmitting}
      destroyOnHidden
      onOk={() => {
        confirm().catch(() => undefined);
      }}
      onCancel={onCancel}
    >
      <Form form={form} layout="vertical" preserve={false} requiredMark>
        <Typography.Paragraph>{description}</Typography.Paragraph>
        <Form.Item
          name="reason"
          label={t("reasonDialog.reasonLabel")}
          extra={t("reasonDialog.reasonHelp")}
          rules={[
            {
              required: true,
              whitespace: true,
              message: t("fieldErrors.missing"),
            },
          ]}
        >
          <Input.TextArea
            autoFocus
            rows={3}
            maxLength={REASON_MAX_LENGTH}
            showCount
            placeholder={t("reasonDialog.reasonPlaceholder")}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
