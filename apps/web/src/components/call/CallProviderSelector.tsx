import type { CallProvider, CallProviderOption } from './call-provider';
import './call-provider.css';

export function CallProviderSelector({ value, locked = false, options, onChange, disabled = false }: {
  value: CallProvider; locked?: boolean; options?: CallProviderOption[];
  onChange: (provider: CallProvider) => void; disabled?: boolean;
}) {
  // Older servers and non-organizer projections expose no controls.
  if (!options?.length) return null;
  return <label className="md-call-provider"><span>Видеосвязь</span><select aria-label="Видеосервис встречи" value={value} disabled={locked || disabled}
    onChange={event => { const provider = event.target.value; if (provider === 'daily' || provider === 'zoom') onChange(provider); }}>
    {(['daily', 'zoom'] as const).map(provider => <option key={provider} value={provider} disabled={!options.some(option => option.id === provider && option.available)}>{provider === 'zoom' ? 'Zoom' : 'Daily'}{options.some(option => option.id === provider && option.available) ? '' : ' · недоступен'}</option>)}
  </select>{locked && <small>Сервис выбран для этой встречи</small>}</label>;
}
