# CLAUDE.md

## Commit de emergência: tudo que não está no .gitignore vai pro GitHub

Este repositório entra no `D:\emergency-push.cmd`, que roda `git add .`,
`git commit -m "Emergency"` e `git push` sem ninguém olhar o diff. Então
**qualquer arquivo na pasta que não esteja no `.gitignore` acaba no remoto**.
Regras:

- Criou arquivo com credencial, dado pessoal, banco, dump, backup ou binário
  grande? Cubra no `.gitignore` **no mesmo passo** e confira com
  `git check-ignore -v <arquivo>`. Não deixe para "depois do commit".
- Nunca escreva segredo de verdade em arquivo versionado: código, README,
  docs, planos, scripts de teste. Use variável de ambiente e deixe o
  `.env.example` só com valores vazios ou fictícios.
- Arquivo acima de 50 MB fica fora do repositório. O GitHub recusa arquivos
  acima de 100 MB e aí o push do repositório inteiro falha.
- Viu um arquivo sensível sem rastreio e fora do `.gitignore`? Ignore o
  arquivo na hora e avise o usuário. Se ele já foi commitado, avise também:
  tirar do índice não apaga do histórico, e a credencial precisa ser trocada.

Específico deste repositório (**PÚBLICO**, qualquer pessoa lê):
- O `.env` fica ignorado. O `.env.example` é versionado e só pode ter valores de exemplo.
- `ACServerFiles/`, `ACServerFiles_montado/`, `servidores/` e `runs/` ficam
  fora: o executável passa de 100 MB e o `content/` traz carros e pistas do jogo.
  Senha de admin do AssettoServer, chave de API ou IP privado nunca entram em
  arquivo versionado.
