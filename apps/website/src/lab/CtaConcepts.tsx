import React from 'react';
import type { SceneControl } from '../site';
import { LOGO_URL } from '../assets';
import { CountdownButton, type CtaVariant } from '../CountdownButton';
import './cta.css';

export function CtaConcept({ variant, control, onStart }: { variant: CtaVariant; control: SceneControl; onStart: () => void }) {
  return <section className="cta-demo" aria-label="Кнопка с таймером">
    <header className="cta-demo-header">
      <img src={LOGO_URL} alt="HR Vision"/>
      <div className="cta-button-wrap">
        <CountdownButton variant={variant} progress={control.progress} playing={control.playing} onStart={() => { control.pause(); onStart(); }}/>
      </div>
    </header>
    <div className="cta-demo-focus">
      <h2>Найдём того,<br/><em>кто решит вашу задачу.</em></h2>
    </div>
  </section>;
}
