import { forwardRef, type ComponentPropsWithoutRef } from 'react';
import { UserRound } from 'lucide-react';
import './account-avatar.css';

type AccountAvatarProps = ComponentPropsWithoutRef<'button'> & { name: string };

/** Shared account control for candidate and employer headers. */
export const AccountAvatar = forwardRef<HTMLButtonElement, AccountAvatarProps>(function AccountAvatar(
  { name, className = '', ...props }, ref,
) {
  const initials = name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map(part => [...part][0]).join('').toUpperCase();
  return <button ref={ref} type="button" title={name || 'Личный кабинет'} aria-label={name ? `Личный кабинет: ${name}` : 'Личный кабинет'} {...props} className={`account-avatar ${className}`.trim()}>
    {initials ? <span aria-hidden="true">{initials}</span> : <UserRound size={18} aria-hidden="true"/>}
  </button>;
});
