// デジタル庁デザインシステムのサンプルコンポーネントから移植（MIT License, Copyright (c) 2025 デジタル庁）。
// 出典: https://github.com/digital-go-jp/design-system-example-components-react
//       src/components/Slot/Slot.tsx （commit 88110f7, package v2.7.0）
// npm未公開のため、必要なコンポーネントをこのディレクトリに個別コピーしている。
// 更新するときは出典コミットも書き換えること。
import { Children, cloneElement, type HTMLAttributes, isValidElement, type ReactNode } from "react";

type SlotProps = HTMLAttributes<HTMLElement> & {
  children?: ReactNode;
};

export const Slot = (props: SlotProps) => {
  const { children, ...rest } = props;

  if (isValidElement(children)) {
    return cloneElement(
      children as React.ReactElement<Record<string, unknown>>,
      {
        ...rest,
        ...(children.props as Record<string, unknown>),
        className: `${rest.className ?? ""} ${(children.props as { className?: string }).className ?? ""}`,
      },
    );
  }

  if (Children.count(children) > 1) {
    Children.only(null);
  }

  return null;
};
