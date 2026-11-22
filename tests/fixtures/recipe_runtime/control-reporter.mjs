/** Registra configuração efetiva e censo da execução controle sintética. */
export default class ControlReporter {
  onTestRunStart(specifications) {
    this.specifications = specifications;
  }
  onTestRunEnd(modules) {
    const config = this.specifications[0].project.config;
    const cases = modules.flatMap(module => [...module.children.allTests()]);
    console.log('ROBO_CONTROL=' + JSON.stringify({pool: config.pool, isolate: config.isolate,
      environment: config.environment, planned: this.specifications.length,
      modules: modules.length, cases: cases.length, passed: cases.filter(item => item.result().state === 'passed').length}));
  }
}
