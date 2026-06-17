import { next } from '@vercel/edge';

// Protege todo o site, exceto os assets internos da Vercel (_vercel/*).
export const config = {
  matcher: '/((?!_vercel/).*)',
};

// Porteiro de senha (HTTP Basic Auth).
// A senha vem da variável de ambiente SITE_PASSWORD, definida no painel da Vercel.
// O usuário pode ser qualquer coisa; só a senha é checada.
export default function middleware(request) {
  const senhaConfigurada = process.env.SITE_PASSWORD;

  // Se nenhuma senha foi configurada, libera (evita travar o primeiro deploy).
  if (!senhaConfigurada) return next();

  const auth = request.headers.get('authorization') || '';
  if (auth.startsWith('Basic ')) {
    try {
      const decoded = atob(auth.slice(6)); // "usuario:senha"
      const senha = decoded.slice(decoded.indexOf(':') + 1);
      if (senha === senhaConfigurada) return next();
    } catch (_) {
      /* header malformado → cai no 401 abaixo */
    }
  }

  return new Response('Acesso restrito. Informe a senha para ver o dashboard.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Dashboard do Squad", charset="UTF-8"',
      'content-type': 'text/plain; charset=utf-8',
    },
  });
}
