import './globals.css';

export const metadata = {
  title: 'AMPYTECH Trader Dashboard',
  description: 'ML-Driven Quantitative Stock Trading System',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
