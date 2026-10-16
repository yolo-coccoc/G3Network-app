/**
 * The login form (ACC-04): phone number and password. A wrong pair, a
 * lockout (423) and too many attempts (429) are shown above the form; a 422
 * goes on the fields.
 */
import { Alert, Button, Flex, Form, Input } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { asApiError } from "../../../shared/api/errors";
import type { Me } from "../../../shared/auth/sessionStore";
import { useReportApiError } from "../../../shared/ui/feedback";
import { type LoginCredentials, useLoginMutation } from "../api/mutations";

/**
 * Props of `LoginForm`.
 *
 * Attributes:
 *   onLoggedIn: Called with the caller's profile once the session has started.
 */
interface LoginFormProps {
  onLoggedIn: (me: Me) => void;
}

/** Phone + password login. */
export function LoginForm({ onLoggedIn }: LoginFormProps) {
  const { t } = useTranslation("identity");
  const { t: tCommon } = useTranslation();
  const [form] = Form.useForm<LoginCredentials>();
  const loginMutation = useLoginMutation();
  const reportApiError = useReportApiError();
  const [failureMessage, setFailureMessage] = useState<string | null>(null);

  const logIn = (credentials: LoginCredentials) => {
    setFailureMessage(null);
    loginMutation.mutate(credentials, {
      onSuccess: onLoggedIn,
      onError: (error) => {
        const { kind } = asApiError(error);
        if (kind === "unauthenticated") {
          setFailureMessage(t("login.invalidCredentials"));
        } else if (
          kind === "locked" ||
          kind === "tooManyRequests" ||
          kind === "forbidden"
        ) {
          setFailureMessage(tCommon(`errors.${kind}`));
        } else {
          reportApiError(error, form);
        }
      },
    });
  };

  return (
    <Flex vertical gap="middle">
      {failureMessage !== null && (
        <Alert type="error" showIcon title={failureMessage} />
      )}
      <Form<LoginCredentials>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={logIn}
      >
        <Form.Item
          name="phone_number"
          label={t("login.phoneNumber")}
          rules={[
            {
              required: true,
              whitespace: true,
              message: t("login.phoneRequired"),
            },
          ]}
        >
          <Input
            autoFocus
            inputMode="tel"
            autoComplete="username"
            placeholder={t("login.phonePlaceholder")}
          />
        </Form.Item>
        <Form.Item
          name="password"
          label={t("login.password")}
          rules={[{ required: true, message: t("login.passwordRequired") }]}
        >
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          block
          loading={loginMutation.isPending}
        >
          {t("login.submit")}
        </Button>
      </Form>
    </Flex>
  );
}
