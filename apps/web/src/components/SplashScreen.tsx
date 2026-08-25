export function SplashScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex size-24 items-center justify-center" aria-label="Open SWE splash screen">
        <img alt="Open SWE" className="size-16 object-contain" src="/langchain-logo.png" />
      </div>
    </div>
  );
}
