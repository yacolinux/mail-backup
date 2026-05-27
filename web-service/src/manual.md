# Manual de Usuario — Zimbra Backup System

## 1. Introducción

Zimbra Backup System es una aplicación web para la gestión de copias de seguridad de buzones de correo Zimbra 9 / Postfix en formato Maildir.

### Funcionalidades principales
- Explorar, buscar y visualizar emails respaldados
- Exportar emails en formato **Markdown (.md)**, **PDF (.pdf)** y **Word (.docx)**
- Descargar múltiples emails en un archivo ZIP
- Enviar backups por correo electrónico como adjunto
- Retención de largo plazo con política GFS (Abuelo-Padre-Hijo)
- Panel de administración con configuración completa
- Modo oscuro para uso prolongado

## 2. Acceso al Sistema

### 2.1 Inicio de Sesión

Abra un navegador web (Chrome, Firefox, Edge o Safari) y acceda a la URL del sistema:

- **Producción**: `https://servidor-backup:8080`
- **Desarrollo local**: `http://localhost:8080`

Ingrese su correo electrónico corporativo y contraseña de red. El sistema autentica contra el directorio corporativo (LDAP).

### 2.2 Roles de Usuario

| Rol | Capacidades |
|-----|-------------|
| **Usuario** | Ver y buscar sus propios emails, exportar, enviar por correo, acceder al manual |
| **Administrador** | Todo lo del usuario + ver todas las cuentas, eliminar emails del backup, panel de administración, configuración del sistema |

El rol se asigna automáticamente según su pertenencia al grupo de administradores en el directorio corporativo.

### 2.3 Modo Oscuro

Presione el icono de luna (☾) en la barra superior para activar el modo oscuro. La preferencia se guarda para futuras sesiones.

### 2.4 Barra Lateral

La barra lateral izquierda puede redimensionarse arrastrando su borde derecho. También puede ocultarse completamente con el botón de menú (☰) en la barra superior.

## 3. Dashboard

Al iniciar sesión verá el **Dashboard** con la siguiente información:

### Panel de Estadísticas (Admin)
- **Cuentas backupeadas**: total de buzones con copia de seguridad
- **Emails indexados**: cantidad de correos en la base de datos
- **Snapshots totales**: copias de seguridad históricas
- **Espacio utilizado**: almacenamiento total ocupado

### Estado del Último Backup
- **Estado**: éxito (verde), parcial (amarillo) o fallido (rojo)
- **Fecha y hora** del último backup
- **Cuentas procesadas** y emails nuevos detectados

### Listado de Cuentas (Admin)

Todas las cuentas con copia de seguridad. Funcionalidades:

- **Búsqueda**: escriba en el campo de búsqueda para filtrar por dirección de correo
- **Paginación**: 35 cuentas por página con navegación
- **Ordenamiento**: haga clic en los encabezados para ordenar por email, dominio, cantidad de emails, fecha o estado
- **Acceso directo**: presione "Ver emails" para explorar los correos de una cuenta

## 4. Exploración de Emails

Haga clic en **"Ver emails"** junto a cualquier cuenta para acceder al listado de correos respaldados.

### 4.1 Filtros Disponibles

- **Carpetas**: menú lateral con la estructura de carpetas IMAP (INBOX, Sent, Trash, etc.)
- **Búsqueda por texto**: filtre por asunto o remitente
- **Rango de fechas**: seleccione "Desde" y "Hasta" para acotar resultados

### 4.2 Selección Múltiple

- Marque el checkbox de cada email para seleccionarlo individualmente
- Use el checkbox del encabezado para seleccionar todos los emails visibles (máximo 35 por página)
- Una barra de acciones aparece automáticamente al seleccionar

### 4.3 Exportar Emails

Con emails seleccionados, tiene tres opciones de formato:

- **MD (Markdown)**: texto plano con metadatos en formato YAML — liviano y portable
- **PDF**: documento formateado profesionalmente — ideal para imprimir
- **DOCX (Word)**: documento editable — permite modificar el contenido

#### Opciones de exportación:

1. **Descargar ZIP**: elija el formato desde el menú desplegable. Se genera un archivo `.zip` con todos los emails convertidos.
2. **Enviar por Correo**: abra el modal de envío, seleccione formato y destino, presione "Enviar ZIP". El archivo se adjunta al correo.

### 4.4 Nombrado de Archivos

Los emails exportados se nombran con el siguiente patrón:

`Asunto del email -restored20260527 -mailde20260524.pdf`

Donde `restored` indica la fecha de exportación y `mailde` la fecha original del correo.

### 4.5 Vista Detallada

Haga clic en cualquier fila para ver el contenido completo:

- **Cabeceras**: De, Para, Fecha, Carpeta
- **Cuerpo**: vista HTML o texto plano
- **Adjuntos**: listado con nombre, tipo y tamaño
- **Descargar individual**: botones MD, PDF, DOCX
- **Eliminar** (solo admin): elimina permanentemente del backup

## 5. Panel de Administración

Accesible desde el menú lateral izquierdo (solo administradores).

### 5.1 Acciones Rápidas

- **Ejecutar backup ahora**: dispara un ciclo de backup manual inmediato
- **Aplicar retención**: elimina snapshots expirados según la política configurada
- **Configuración**: abre el panel completo de configuración del sistema

### 5.2 Configuración Activa

Muestra en tiempo real:
- Intervalo de backup programado
- Estado del modo remoto (Zimbra) y del backup secundario (offsite)
- Versionado git de metadatos
- Política de retención (horarios, diarios, semanales, mensuales)
- Espacio total ocupado y cantidad de snapshots

### 5.3 Panel de Configuración

El botón "Configuración" abre una ventana con 8 pestañas:

| Pestaña | Contenido |
|---------|-----------|
| **General** | Rutas de almacenamiento, nivel de log, intervalo de backup, zona horaria |
| **Origen** | Ruta Maildir, método de descubrimiento (zmprov/scan), filtros de cuentas |
| **Zimbra Remote** | Conexión SSH al servidor Zimbra: host, usuario, clave, puerto. Botón "Probar conexión" |
| **Offsite** | Backup secundario: host, usuario, ruta, opciones rsync. Botón "Probar conexión" |
| **Retención** | Cantidad de snapshots a conservar por período (horario/diario/semanal/mensual) |
| **Git** | Versionado de metadatos: repositorio, remote, usuario |
| **LDAP** | Configuración del directorio corporativo: host, bind DN, filtro de usuario. Botones de prueba |
| **Usuarios Locales** | Gestión de usuarios demo (agregar, editar, eliminar). Solo en modo DEMO |
| **Reset** | Desplegar contenido de ejemplo o Factory Reset (borrado completo) |

### 5.4 Historial de Backups

Tabla con los últimos backups ejecutados:
- ID del backup
- Fecha y hora de inicio
- Tipo (manual o programado)
- Estado (éxito, parcial o fallido)
- Cuentas procesadas
- Emails nuevos detectados

## 6. Eliminación de Emails

> **ADVERTENCIA**: La eliminación de emails del backup es **IRREVERSIBLE**. Solo los administradores pueden realizarla.

**Procedimiento:**

1. Acceda al detalle del email (clic en cualquier fila del listado)
2. Presione el botón rojo **"Eliminar definitivamente"**
3. Revise la información en el modal de confirmación
4. Presione **"Sí, eliminar definitivamente"**

El email se eliminará de **todos los snapshots históricos** donde estaba presente. No hay recuperación posible.

## 7. Preguntas Frecuentes

### ¿Cada cuánto se ejecuta el backup automático?
Por defecto, cada 2 horas. El intervalo es configurable por el administrador en la pestaña General de Configuración.

### ¿Cuánto espacio ocupan los backups?
Gracias a los **hardlinks** de rsync, los emails que no cambian entre snapshots ocupan espacio una sola vez en disco. El espacio real usado es proporcional al crecimiento del buzón, no a la cantidad de snapshots. Ejemplo: un buzón de 2 GB con 250 snapshots ocupa ~2.5 GB, no 500 GB.

### ¿Puedo recuperar un email ya eliminado del backup?
No. La eliminación es definitiva y el email se borra de todos los snapshots históricos.

### ¿Puedo restaurar un email a mi buzón Zimbra?
Descargue el email en formato MD, PDF o DOCX y contacte al administrador del sistema de correo para su reinserción manual en el buzón.

### ¿Qué navegadores son compatibles?
Google Chrome, Mozilla Firefox, Microsoft Edge y Apple Safari en sus versiones actuales.

### ¿La información viaja cifrada?
Sí. En producción, la interfaz web debe usar HTTPS. Las conexiones SSH entre el servidor de backup y el servidor Zimbra usan cifrado estándar.

### ¿Qué hago si olvido mi contraseña?
Contacte al administrador de sistemas o al servicio de soporte técnico de su organización. Las credenciales se autentican contra el directorio corporativo (LDAP).

---

*Zimbra Backup System — v0.3.0*

*Desarrollado para operaciones de correo empresarial con retención de largo plazo basada en política GFS.*
