// Placeholder seguro. Se sustituye con npm run build --prefix market-terminal.
document.querySelectorAll('[data-market-terminal]').forEach(node => {
  const help = node.querySelector('p');
  if (help) help.textContent = 'Terminal pendiente de compilar. Ejecuta npm ci y npm run build dentro de market-terminal.';
});
