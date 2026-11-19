/* Busca de ativos no topo do site: carrega busca.json na primeira vez que o campo recebe foco. */
(function () {
  var form = document.querySelector('.busca-topo');
  if (!form) return;
  var raiz = form.dataset.raiz || '', campo = form.querySelector('input'), lista = form.querySelector('ul');
  var indice = null, achados = [], ativo = -1;
  function carregar() {
    if (indice) return Promise.resolve(indice);
    return fetch(raiz + 'busca.json').then(function (r) { return r.json(); }).then(function (j) { indice = j; return j; });
  }
  function normal(t) { return t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function mostrar() {
    lista.innerHTML = '';
    achados.forEach(function (a, i) {
      var li = document.createElement('li'), link = document.createElement('a');
      link.href = raiz + a.u; link.id = 'busca-op-' + i;
      link.innerHTML = '<strong></strong><span></span><em></em>';
      link.children[0].textContent = a.t; link.children[1].textContent = a.n; link.children[2].textContent = a.k;
      if (i === ativo) link.setAttribute('aria-selected', 'true');
      li.appendChild(link); lista.appendChild(li);
    });
    lista.hidden = !achados.length;
    campo.setAttribute('aria-expanded', achados.length ? 'true' : 'false');
  }
  function procurar() {
    var q = normal(campo.value.trim());
    if (!q) { achados = []; mostrar(); return; }
    carregar().then(function (idx) {
      var comeca = [], contem = [];
      idx.forEach(function (a) {
        var t = a.t.toLowerCase();
        if (t.indexOf(q) === 0) comeca.push(a);
        else if (normal(a.n).indexOf(q) > -1) contem.push(a);
      });
      achados = comeca.concat(contem).slice(0, 8); ativo = achados.length ? 0 : -1; mostrar();
    });
  }
  campo.addEventListener('focus', carregar, { once: true });
  campo.addEventListener('input', procurar);
  campo.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (!achados.length) return;
      ativo = (ativo + (e.key === 'ArrowDown' ? 1 : -1) + achados.length) % achados.length; mostrar();
    } else if (e.key === 'Escape') { achados = []; mostrar(); }
  });
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (achados[ativo]) location.href = raiz + achados[ativo].u;
  });
  document.addEventListener('click', function (e) { if (!form.contains(e.target)) { achados = []; mostrar(); } });
})();
