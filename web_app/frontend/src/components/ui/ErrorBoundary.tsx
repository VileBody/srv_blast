import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode; title?: string };
type State = { error?: Error };

export class ErrorBoundary extends Component<Props, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <section className="card-2 flex min-h-[320px] flex-col items-center justify-center px-[40px] text-center">
          <img src="/assets/figma/logo-star.svg" width="48" height="48" alt="Blast" />
          <h2 className="mt-[24px] text-[28px] font-[400] leading-[34px] text-text">{this.props.title ?? 'Что-то пошло не так'}</h2>
          <p className="mt-[10px] max-w-[520px] text-[16px] font-[350] leading-[20px] text-text-60">Один из блоков не загрузился. Остальная оболочка приложения продолжает работать.</p>
          <button type="button" onClick={() => this.setState({ error: undefined })} className="mt-[24px] flex h-[60px] items-center justify-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] font-[350] leading-none text-text-80 transition hover:text-text">
            Повторить
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}
