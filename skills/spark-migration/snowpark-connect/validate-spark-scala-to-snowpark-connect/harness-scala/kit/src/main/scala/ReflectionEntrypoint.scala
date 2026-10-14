// Ported from: validate-pyspark-to-snowpark-connect/scripts/harness/test_template.py
//              (importlib.util.spec_from_file_location / module.exec_module pattern)
//
// ReflectionEntrypoint: loads a compiled workload JAR and invokes an entrypoint
// method by JVM reflection.  Replaces Python's importlib-based dynamic module load.
//
// JVM NOTE: Unlike Python importlib, the JVM does NOT allow reloading a class once
// loaded by a classloader.  Use a fresh URLClassLoader per trial to isolate the
// workload JAR from the harness classpath.  No sitecustomize/loader/fallback needed.

package com.snowflake.scos.kit

import java.io.File
import java.lang.reflect.Method
import java.net.URLClassLoader
import scala.util.Try

/**
 * Loads a compiled workload JAR entry point via JVM reflection.
 *
 * Usage:
 * {{{
 *   val ep = ReflectionEntrypoint.load(
 *     jarPath       = "lib/my-workload.jar",
 *     className     = "com.example.MyJob",
 *     methodName    = "run",
 *   )
 *   ep.invoke(spark)                          // passes SparkSession as first arg
 *   ep.invokeMain(Array("--env", "dev"))       // calls object's main(args)
 * }}}
 *
 * @param method       reflected Method to invoke
 * @param instance     object instance (null for Scala objects / static Java methods)
 * @param classLoader  classloader that owns the workload JAR
 */
case class ReflectionEntrypoint(
    method:      Method,
    instance:    AnyRef,
    classLoader: URLClassLoader,
) extends AutoCloseable {

  /** Unwrap reflection's InvocationTargetException so callers see the real workload error. */
  private def unwrap[T](thunk: => T): T =
    try thunk
    catch {
      case e: java.lang.reflect.InvocationTargetException =>
        throw Option(e.getCause).getOrElse(e)
    }

  /**
   * Call the reflected method with a SparkSession as the first argument.
   * Handles both `def run(spark: SparkSession)` and `def run(spark: SparkSession, ...kwargs)`.
   */
  def invoke(args: AnyRef*): AnyRef = {
    val prevCl = Thread.currentThread().getContextClassLoader
    Thread.currentThread().setContextClassLoader(classLoader)
    try {
      unwrap(method.invoke(instance, args: _*))
    } finally {
      Thread.currentThread().setContextClassLoader(prevCl)
    }
  }

  /** Call `main(Array[String])` on the entry-point object (or, for a static Java
    * `main`, on its declaring class directly — `instance` is null in that case). */
  def invokeMain(args: Array[String] = Array.empty): Unit = {
    val prevCl = Thread.currentThread().getContextClassLoader
    Thread.currentThread().setContextClassLoader(classLoader)
    try {
      val declaringClass = if (instance != null) instance.getClass else method.getDeclaringClass
      val mainMethod = declaringClass.getMethod("main", classOf[Array[String]])
      unwrap(mainMethod.invoke(instance, args.asInstanceOf[AnyRef]))
    } finally {
      Thread.currentThread().setContextClassLoader(prevCl)
    }
    ()
  }

  /** Close the workload classloader to release JAR file handles (call in test afterAll). */
  override def close(): Unit = Try(classLoader.close())
}

object ReflectionEntrypoint {

  /**
   * Load an entry point from a workload JAR.
   *
   * @param jarPath    Path to the compiled workload JAR (absolute or relative to CWD).
   *                   Defaults to the first *.jar found in lib/.
   * @param className  Fully-qualified class name, e.g. "com.example.jobs.MyJob$".
   *                   Use trailing "$" for Scala objects.  Use plain name for Java classes.
   * @param methodName Method to invoke (defaults to "run"; also accepts "main").
   * @param extraJars  Additional JARs to include on the classloader (utilities, etc.).
   */
  def load(
      jarPath:    String,
      className:  String,
      methodName: String     = "run",
      extraJars:  Seq[String] = Nil,
  ): ReflectionEntrypoint = {
    val jarFile = new File(jarPath)
    require(jarFile.isFile, s"Workload JAR not found: $jarPath")

    // Build URLClassLoader with the workload JAR + any extras that still exist
    // on disk (stale export paths across worktrees must not poison the loader).
    val existingExtras = extraJars.filter { p =>
      val f = new File(p)
      val ok = f.isFile
      if (!ok && p != null && p.nonEmpty) {
        System.err.println(s"warn: ReflectionEntrypoint skipping missing extraJar: $p")
      }
      ok
    }
    val urls = (Seq(jarFile) ++ existingExtras.map(new File(_)))
      .map(_.toURI.toURL)
      .toArray
    val cl = new URLClassLoader(urls, Thread.currentThread().getContextClassLoader)

    // Load the class and resolve the method first: its static-ness determines
    // whether an instance is needed at all (Java `public static void main`/`run`
    // entrypoints — the common Java workload shape — need none).
    val clazz = cl.loadClass(className)
    val method = findMethod(clazz, methodName)
      .getOrElse(sys.error(s"Method '$methodName' not found on $className. " +
                            s"Available: ${clazz.getMethods.map(_.getName).distinct.sorted.mkString(", ")}"))
    method.setAccessible(true)

    // Scala objects are accessed via the MODULE$ field on the module class (name
    // ends with "$"). If the caller omitted the "$" suffix, try the module class
    // transparently before falling back to the no-arg constructor. Static methods
    // (Java entrypoints, and Scala's own static forwarder methods) need no
    // instance at all — `Method.invoke` ignores the receiver for a static method.
    val instance: AnyRef =
      if (java.lang.reflect.Modifier.isStatic(method.getModifiers)) null
      else getScalaObjectInstance(clazz).orElse {
        if (!className.endsWith("$"))
          Try(cl.loadClass(className + "$")).toOption.flatMap(getScalaObjectInstance)
        else None
      }.getOrElse {
        val ctor = clazz.getDeclaredConstructor()
        ctor.setAccessible(true)
        ctor.newInstance().asInstanceOf[AnyRef]
      }

    ReflectionEntrypoint(method, instance, cl)
  }

  /**
   * Scan lib/ for the first workload JAR and return a loader for it.
   * Convenience for rendered test specs that don't hard-code the JAR path.
   */
  def fromLibDir(
      libDir:     String = "lib",
      className:  String,
      methodName: String = "run",
  ): ReflectionEntrypoint = {
    val jars = new File(libDir).listFiles(_.getName.endsWith(".jar"))
    require(jars != null && jars.nonEmpty, s"No JAR files found in $libDir/")
    load(jars.sortBy(_.getName).head.getAbsolutePath, className, methodName)
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  /** Try to obtain the singleton instance of a Scala `object`. */
  private def getScalaObjectInstance(clazz: Class[_]): Option[AnyRef] =
    Try {
      val field = clazz.getField("MODULE$")
      field.setAccessible(true)
      field.get(null)
    }.toOption

  /**
   * Find a public method by name on the class or any of its superclasses.
   * Prefers methods whose parameter list starts with SparkSession.
   */
  private def findMethod(clazz: Class[_], methodName: String): Option[Method] = {
    val candidates = allMethods(clazz).filter(_.getName == methodName)
    // Prefer: (SparkSession, ...) signature (typical workload entry)
    candidates
      .find(m => m.getParameterTypes.headOption.exists(_.getName.contains("SparkSession")))
      .orElse(candidates.headOption)
  }

  private def allMethods(clazz: Class[_]): Seq[Method] = {
    var c: Class[_] = clazz
    val buf         = collection.mutable.ListBuffer[Method]()
    while (c != null) {
      buf ++= c.getDeclaredMethods
      c = c.getSuperclass
    }
    buf.toSeq
  }
}
