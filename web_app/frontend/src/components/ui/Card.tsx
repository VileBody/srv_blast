import type { HTMLAttributes } from 'react';
import { cn } from '../../lib/cn';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('card', className)} {...props} />;
}

export function FlatCard({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('card-flat', className)} {...props} />;
}
