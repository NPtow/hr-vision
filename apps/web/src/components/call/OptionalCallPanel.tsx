import { Component, type ReactNode } from 'react';

/** This boundary encloses optional content only. Never wrap the live frame in it. */
export class OptionalCallPanel extends Component<{ children: ReactNode; label: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="rcc-script-empty" role="alert"><p>{this.props.label} временно недоступны. Звонок продолжается.</p><button onClick={() => this.setState({ failed: false })}>Открыть повторно</button></div>;
  }
}
