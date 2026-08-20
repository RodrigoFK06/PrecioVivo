# Postmortem · Nueve fallos que devolvían éxito

**Periodo:** 2026-08-18 a 2026-08-20 · **Sistema:** Precio Vivo (pipeline, AWS, sitio)
**Estado:** cerrado, con cambios verificados
**Formato:** sin culpables. Ninguno de estos fallos se corrigió señalando a quien
lo escribió — todos los escribí yo durante esta misma iteración.

---

## Resumen

Migrando el pipeline diario a AWS aparecieron **nueve fallos con la misma
forma**: el sistema hacía algo mal y reportaba que había ido bien. Ninguno
produjo una excepción, un código de salida distinto de cero ni una alarma.

Ocho de los nueve los encontró una persona mirando un número que no cuadraba. El
noveno lo encontró CI, y solo porque ese mismo día se desbloqueó.

Ese es el hallazgo que importa: **este sistema tenía una tendencia estructural a
fallar hacia arriba.** Cada pieza estaba escrita para degradar con elegancia —lo
cual es correcto de cara al usuario— y ninguna estaba escrita para dejar rastro.
La suma de muchas degradaciones elegantes es un sistema que no se puede
diagnosticar.

**Impacto máximo evitado:** una corrida diaria habría consumido el 30 % de la
cuota anual de embeddings, en silencio, agotándola en tres días.

**Impacto real sobre usuarios:** el sitio respondió durante semanas afirmando que
hacía recuperación vectorial sin hacerla, y negó la existencia de un producto que
sí publicaba. Ambos, con 200 y datos reales en pantalla.

---

## Los nueve, en una tabla

| # | Dónde | Qué hacía | Qué lo delató |
|---|---|---|---|
| 1 | Índice RAG publicado | Firma `local:` inusable desde Vercel; `rag.ts` lo detectaba, caía al piso determinista y **seguía etiquetando `llm-rag`** | Contar los chunks que el sitio veía: 165 de 9.206 |
| 2 | `ingest.main()` | `return 0` incondicional: un cambio de layout del PDF dejaba la tarea diaria en verde | Leer el final de la función |
| 3 | `/api/consulta` | `catch` vacíos: «sin clave», «clave inválida» y «firma incompatible» daban el mismo 200 | Ninguno — hubo que **añadir** el registro para poder diagnosticar |
| 4 | `_add_verificacion` | `ModuleNotFoundError` capturado en silencio; el sitio sin insignia de verificación | Una clave `verificacionError` dentro del JSON |
| 5 | Corpus RAG | El asistente negaba que existiera el pollo vivo, con el precio en el mismo JSON | Preguntarle por él |
| 6 | `cdk deploy` (×2) | Salió con **código 0 sin desplegar** por un bloqueo de `cdk.out` | `chunks_totales: 9280` cuando debía ser 9281 |
| 7 | Guard del índice | Comparaba contra `pipeline/.env`, un archivo no versionado: en local pasaba por casualidad | CI, en su primera corrida tras desbloquearse |
| 8 | Detector de invenciones | Descartaba todo número < 10 — es decir, **todos los precios** — y reportaba «0 invenciones» | Desconfiar de un 0 % perfecto y probar el detector |
| 9 | Relevo de SISAP | Tarea programada fallando; la tubería de AWS terminaba en verde igual | Revisar `LastTaskResult` a mano |

---

## Tres en detalle

### Incidente 1 · El RAG que no era RAG

**Detección.** Nadie lo reportó. Apareció contando cuántos chunks tenía el
artefacto publicado frente a cuántos decía tener el corpus.

**Cronología.** El índice se construyó con el embebedor local (`model2vec`) y se
publicó. Vercel no puede correr un modelo local, así que embebe la consulta por
HTTP con otra firma. `rag.ts` compara firmas, no coinciden, lanza — y
`recuperar()` captura la excepción y devuelve solo el piso determinista. Como el
piso trae chunks reales, la respuesta se sirve **y se etiqueta `llm-rag`**.

**Causa raíz.** No fue el embebedor equivocado. Fue que el único control era un
`print` de advertencia en `indexer.py`. *Un aviso que se puede ignorar no es un
control.*

**Por qué tardó.** Toda la disciplina de verificación apuntaba al DATO —el
dead-man's-switch, las evals de recall— y ninguna al ARTEFACTO. El artefacto no
es código: ningún test unitario lo mira.

**Qué cambió.** Nueve pruebas marcadas `publicado` que verifican el archivo que
se sirve: las cuatro partes existen, la firma es la que usará el sitio, ambas
partes comparten embebedor, el `.bin` cuadra con `n_chunks × dims`. Y publicar un
índice `local:` pasa de aviso a error.

---

### Incidente 8 · El detector que no detectaba

**Detección.** Un 0 % perfecto en la primera medición. No un fallo: un resultado
demasiado limpio.

**Cronología.** Se escribió un arnés para medir si las respuestas inventan
cifras. Primera corrida: «203 cifras afirmadas, 0 sin respaldo». En vez de
publicar el número, se le dieron al detector seis invenciones conocidas que
**debía** marcar. Falló cuatro de seis.

**Causa raíz.** Una línea: `if v < 10: continue`, puesta para no ahogarse en
ruido de conteos («los 5 más baratos»). Descartaba, con eso, **todos los
precios** — S/ 1.03, S/ 4.87, un +3,0 %. El arnés solo había mirado toneladas y
fechas. La cifra que este producto promete no inventar era justamente la que no
se medía.

**Por qué es el más instructivo.** Los otros ocho fallaban en silencio. Éste
**afirmaba activamente que todo iba bien**, con un número de aspecto respetable.
Si se hubiera publicado, habría sido peor que no medir nada: una métrica falsa
desactiva la sospecha.

**Qué cambió.** El detector se prueba contra invenciones conocidas, no contra su
propia salida. Doce pruebas fijan cada categoría, con la regresión nombrada. Y el
número real, con el detector ya verificado: **352 cifras, 0 sin respaldo,
abstenciones 2/2, USD 0,001437 por consulta.**

---

### Incidente 9 · El relevo que no se enteró de que había fallado

**Detección.** Comprobando a mano la primera corrida autónoma.

**Cronología.** 2026-08-20, 08:00 Lima: la tubería de AWS corre sola de punta a
punta y termina en **SUCCEEDED**. A las 09:10 —hora y media tarde— la tarea de
Windows que releva SISAP se ejecuta y falla con código 1.

**Causa raíz.** `WakeToRun: False` con `StartWhenAvailable: True`. La máquina
estaba dormida a su hora; Windows la ejecutó al despertar, con la pila de red aún
sin levantar, y el primer `aws s3 cp` falló en seco. Además corrió **después** de
la tubería, rompiendo el orden que el diseño necesita.

**Por qué nadie se habría enterado.** Tres vigilantes y ninguno cubría esto:

- La tubería de AWS terminó en verde, y **correctamente**: el contraste no es
  bloqueante por diseño.
- Las alarmas de CloudWatch vigilan la máquina de estados, no un portátil.
- El dead-man's-switch vigilaba la frescura del DATO, que estaba impecable — el
  contraste puede morir sin que nada envejezca.

**Qué cambió.** El dead-man's-switch vigila también el contraste, con umbral
propio y más laxo (3 días hábiles contra 2): dato atrasado es una avería del
pipeline, contraste atrasado es una pérdida de verificación, y empatar los
umbrales convertiría un incidente menor en una alarma de las que se ignoran. El
relevo espera a que haya red antes de empezar. La tarea pasa a `WakeToRun` con
dos reintentos.

---

## Causa raíz común

Los nueve comparten una decisión de diseño **correcta** llevada demasiado lejos:
*el usuario siempre recibe una respuesta con datos reales.*

Esa decisión es buena. Un sitio de precios que devuelve 500 es peor que uno que
degrada. El problema es que se aplicó **sin su contrapartida**: degradar bien de
cara al usuario exige gritar hacia dentro. Ocho de los nueve degradaban en
silencio absoluto.

La forma concreta que tomó, tres variantes:

1. **El `catch` vacío.** Se traga el error para no romper la respuesta, y con él
   la única pista de qué pasó. (#3, #4)
2. **El valor por defecto que nadie usa.** `return 0`, `EMBED_MODEL =
   text-embedding-3-small`, `if v < 10`. Un default que no describe el
   despliegue real es una trampa esperando a que falte una variable. (#2, #7, #8)
3. **El vigilante que mira otra cosa.** Todo apuntaba al dato; nada al artefacto,
   al contraste ni al proceso. (#1, #9)

---

## Acciones, con lo que cambió de verdad

| # | Acción | Verificación |
|---|---|---|
| 1 | Guards sobre el artefacto publicado, no solo sobre el código | 9 pruebas `publicado`, herméticas tras el incidente #7 |
| 2 | La compuerta de escritura deriva a un estado `Fail` visible | Probado: la ejecución queda en rojo |
| 3 | Cada peldaño de `/api/consulta` registra por qué cedió, sin filtrar la clave | Diagnosticó el 401 en 20 minutos |
| 4 | Alarmas CloudWatch → SNS → correo | Verificado que **ejecuta la acción**, no solo que exista |
| 5 | Dead-man's-switch vigila dato **y** contraste, con umbrales distintos | 7 pruebas, que no tenía ninguna |
| 6 | CI desbloqueado y ampliado a `infra` y `scripts` | Encontró 3 fallos que el local no podía ver |
| 7 | Los detectores se prueban contra fallos conocidos | 12 pruebas del detector de invenciones |
| 8 | El orden de imports pasa de comentario a invariante probado | Verificado invirtiéndolo a propósito: lo caza |
| 9 | Defaults que describen el despliegue real | Perder una variable degrada a error visible, no a silencio |

**459 pruebas** de pipeline, **218** del sitio, cuatro trabajos de CI en verde.

---

## Lo que queda abierto

- **SISAP sigue dependiendo de una máquina encendida en Lima.**
  `sistemas.midagri.gob.pe` no acepta conexiones desde AWS (medido: 200 en 0,3 s
  desde Perú, `ConnectTimeout` a los 60 s desde `us-east-1`). El relevo está
  endurecido, no resuelto.
- **El bloqueo de `cdk.out` (#6) no está arreglado**, solo diagnosticado. Un
  `cdk deploy` interrumpido deja el candado y el siguiente sale con código 0 sin
  desplegar. Mitigación actual: comprobar el timestamp del stack en vez del
  código de salida.
- **Tres claves de API sin rotar.**

---

## Lo que me llevo

**Un indicador que siempre sale perfecto es indistinguible de uno roto.** La
única forma de separarlos es enseñarle algo que tiene que marcar. Es la lección
del incidente #8 y la que más cambió cómo se escribe aquí: cada guard nuevo de
esta iteración se verificó rompiendo algo a propósito.

**Fallar de forma visible no es lo mismo que notificar.** El diseño llegó hasta
«queda registrado en la consola de Step Functions» y se detuvo ahí. Nadie mira
esa consola a las ocho de la mañana.

**Un proceso que falla y devuelve éxito es más caro que uno que se cae.** Nadie
busca un problema que no se ha anunciado. Los nueve tardaron entre horas y
semanas en aparecer; ninguno habría sobrevivido diez minutos si hubiera devuelto
un código distinto de cero.
