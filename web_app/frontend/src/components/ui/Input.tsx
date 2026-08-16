import type { InputHTMLAttributes, TextareaHTMLAttributes } from 'react';
import { cn } from '../../lib/cn';

export function Input({ className, 'aria-invalid': invalid, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('input', invalid && 'input-error', className)} aria-invalid={invalid} {...props} />;
}

export function Textarea({ className, 'aria-invalid': invalid, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('input min-h-[140px] resize-y', invalid && 'input-error', className)} aria-invalid={invalid} {...props} />;
}

export function FieldError({ children }: { children?: string | false | null }) {
  if (!children) return null;
  return <div className="field-error" role="alert">{children}</div>;
}
